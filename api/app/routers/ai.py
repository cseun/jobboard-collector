"""계정별 AI 서비스 키(ChatGPT·Claude·Gemini) 등록 + 모델 선택 + AI 호출 중계.

- 키는 암호화해서 저장하고(평문 저장·로그·응답 없음), 호출할 때만 서버에서 복호화합니다. 브라우저로는 끝 4자리만 내려갑니다.
- 브라우저는 각 AI 서비스에 직접 접속하지 않고 이 서버를 거칩니다(키가 브라우저·화면에 노출되지 않음).
- 키를 등록하면 그 키로 쓸 수 있는 모델 목록을 서비스에서 받아 와 기본·빠른 모델을 자동으로 고르고, 사용자가 목록에서 바꿀 수 있습니다.
- 계정당 분당·하루 호출 수를 제한하고, 질문·답변 내용은 저장하지 않습니다(호출 수·토큰 수만 기록).
"""
import base64
import binascii
import json
import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from .. import config, crypto
from ..db import get_db
from ..deps import get_current_user
from ..models import AiUsage, DemoUser, User, UserAiKey, UserApiKey, utcnow

router = APIRouter(prefix="/api/ai", tags=["ai"])

_IMG_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_ANTHROPIC_VERSION = "2023-06-01"
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,99}$")
_MODEL_CACHE_SEC = 600

# 서비스별 정보. 키 형식은 '명백히 다른 글자'를 걸러 내는 용도이고, 진짜 확인은 모델 목록 조회로 합니다.
PROVIDERS: dict[str, dict] = {
    "openai": {
        "label": "ChatGPT", "vendor": "OpenAI", "hint": "sk-proj-…",
        "key_re": re.compile(r"^sk-(?!ant-)[A-Za-z0-9_\-]{20,300}$"),
        "console": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "label": "Claude", "vendor": "Anthropic", "hint": "sk-ant-api03-…",
        "key_re": re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,300}$"),
        "console": "https://console.anthropic.com/settings/keys",
    },
    "gemini": {
        "label": "Gemini", "vendor": "Google", "hint": "AIza… 또는 AQ.…",
        "key_re": re.compile(r"^(AIza[A-Za-z0-9_\-]{30,150}|AQ\.[A-Za-z0-9_\-.]{20,200})$"),
        "console": "https://aistudio.google.com/apikey",
    },
}


def _err(status: int, code: str, msg: str) -> JSONResponse:
    # 401은 쓰지 않습니다(브라우저 쪽에서 '로그인 만료'로 처리해 로그인 화면으로 보내기 때문).
    return JSONResponse({"detail": msg, "code": code}, status_code=status)


class _Window:
    """계정별 슬라이딩 윈도우 제한(프로세스 메모리, 워커 1개 기준)."""

    def __init__(self, limit: int, seconds: int):
        self.limit, self.seconds = limit, seconds
        self._hits: dict[int, deque] = defaultdict(deque)

    def _prune(self, uid: int, now: float):
        q = self._hits[uid]
        while q and now - q[0] > self.seconds:
            q.popleft()

    def full(self, uid: int) -> bool:
        self._prune(uid, time.monotonic())
        return len(self._hits[uid]) >= self.limit

    def add(self, uid: int):
        self._hits[uid].append(time.monotonic())

    def clear_all(self):
        self._hits.clear()


per_minute = _Window(0, 60)       # limit은 요청 시점에 config 값으로 맞춥니다(테스트에서 바꿀 수 있게)
key_attempts = _Window(10, 600)   # 키 등록 시도: 10분에 10번(형식이 틀려 서비스에 보내지 않은 시도는 세지 않음)


_models_cache: dict[tuple[int, str], tuple[float, list[str]]] = {}


def reset_limits():
    per_minute.clear_all()
    key_attempts.clear_all()
    _models_cache.clear()


def _today() -> str:
    return (utcnow() + timedelta(hours=9)).date().isoformat()  # 한국 시간 기준 날짜


def _is_demo(db: DBSession, user: User) -> bool:
    return db.get(DemoUser, user.id) is not None


def _usage(db: DBSession, uid: int) -> AiUsage | None:
    return db.get(AiUsage, (uid, _today()))


# ---------------------------------------------------------------- 저장된 키 읽기
def _rows(db: DBSession, uid: int) -> dict[str, UserAiKey]:
    """이 계정의 서비스별 키. 예전(Claude 하나만 저장하던) 표에 키가 있으면 새 표로 한 번 옮겨 담습니다."""
    rows = {r.provider: r for r in db.query(UserAiKey).filter(UserAiKey.user_id == uid).all()}
    old = db.get(UserApiKey, uid)
    if old is not None:
        if "anthropic" not in rows:
            r = UserAiKey(
                user_id=uid, provider="anthropic", ciphertext=old.ciphertext, last4=old.last4,
                model_default=config.AI_MODEL_DEFAULT, model_quick=config.AI_MODEL_QUICK,
                active=not any(x.active for x in rows.values()),
            )
            db.add(r)
            rows["anthropic"] = r
        db.delete(old)
        db.commit()
    return rows


def _active_row(rows: dict[str, UserAiKey]) -> UserAiKey | None:
    for r in rows.values():
        if r.active:
            return r
    return next(iter(rows.values()), None)


def _status(db: DBSession, user: User) -> dict:
    rows = _rows(db, user.id)
    act = _active_row(rows)
    u = _usage(db, user.id)
    providers = {}
    for p, info in PROVIDERS.items():
        r = rows.get(p)
        providers[p] = {
            "label": info["label"], "vendor": info["vendor"], "hint": info["hint"], "console": info["console"],
            "configured": r is not None, "last4": r.last4 if r else None,
            "model_default": r.model_default if r else "", "model_quick": r.model_quick if r else "",
        }
    return {
        "server_ready": crypto.enabled(),
        "demo": _is_demo(db, user),
        "configured": act is not None,
        "last4": act.last4 if act else None,
        "active": act.provider if act else None,
        "providers": providers,
        "today": {"used": u.calls if u else 0, "limit": config.AI_DAILY_LIMIT},
    }


# ---------------------------------------------------------------- 서비스 호출(공통)
class _AiError(Exception):
    def __init__(self, status: int, code: str, msg: str):
        super().__init__(msg)
        self.status, self.code, self.msg = status, code, msg


def _headers(provider: str, key: str) -> dict:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION, "content-type": "application/json"}
    if provider == "openai":
        return {"authorization": f"Bearer {key}", "content-type": "application/json"}
    return {"x-goog-api-key": key, "content-type": "application/json"}  # gemini: 키를 주소가 아닌 헤더로 보냅니다(로그에 안 남게)


def _base(provider: str) -> str:
    return {"anthropic": config.ANTHROPIC_BASE_URL, "openai": config.OPENAI_BASE_URL, "gemini": config.GEMINI_BASE_URL}[provider]


def _err_text(r) -> tuple[str, str]:
    """서비스 오류 응답에서 (메시지, 코드·상태 문자열)을 뽑습니다."""
    try:
        e = r.json().get("error")
    except Exception:
        return "", ""
    if isinstance(e, dict):
        return str(e.get("message") or ""), " ".join(str(e.get(k) or "") for k in ("code", "type", "status"))
    return "", ""


def _map_error(provider: str, r) -> _AiError:
    s = r.status_code
    em, tag = _err_text(r)
    low, tag = em.lower(), tag.lower()
    label = PROVIDERS[provider]["label"]
    if s in (401, 403) or (provider == "gemini" and s == 400 and ("api key not valid" in low or "api_key_invalid" in tag)):
        return _AiError(424, "invalid_key", f"등록한 {label} API 키가 거절됐어요. 키를 지웠거나 만료됐을 수 있어요. 설정에서 다시 등록해 주세요.")
    if s == 429 and "insufficient_quota" in tag:
        return _AiError(402, "no_credit", f"{label} API 크레딧(결제 한도)이 부족해요. {PROVIDERS[provider]['vendor']} 계정에서 확인한 뒤 다시 시도해 주세요.")
    if s == 429:
        return _AiError(429, "rate_limited", f"{label} 요청이 잠시 많거나 사용 한도에 걸렸어요. 1분 뒤 다시 눌러 주세요.")
    if s == 400 and "credit balance" in low:
        return _AiError(402, "no_credit", "Claude API 크레딧이 부족해요. console.anthropic.com에서 충전한 뒤 다시 시도해 주세요.")
    if s == 404 or (s == 400 and "model" in low and ("not found" in low or "does not exist" in low or "not supported" in low)):
        return _AiError(400, "bad_model", f"선택한 {label} 모델을 쓸 수 없어요. 설정에서 다른 모델을 골라 주세요.")
    if s == 413:
        return _AiError(413, "too_large", "보내는 내용이 너무 커요. 줄여서 다시 시도해 주세요.")
    if s in (500, 502, 503, 504, 529):
        return _AiError(502, "upstream_error", f"{label} 서버가 바쁘거나 점검 중이에요. 잠시 뒤 다시 시도해 주세요.")
    return _AiError(502, "upstream_error", f"{label} 요청이 거절됐어요(코드 {s}). 같은 문제가 계속되면 알려 주세요.")


def _http(provider: str, method: str, url: str, key: str, *, params=None, body=None, timeout=None):
    try:
        if method == "GET":
            return httpx.get(url, params=params, headers=_headers(provider, key), timeout=timeout)
        return httpx.post(url, headers=_headers(provider, key), json=body, timeout=timeout)
    except httpx.TimeoutException:
        raise _AiError(504, "timeout", "AI 답이 너무 오래 걸려요. 잠시 뒤 다시 눌러 주세요.")
    except httpx.HTTPError:
        raise _AiError(502, "upstream_error", "AI 서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.")


# ---------------------------------------------------------------- 모델 목록
def _vkey(mid: str) -> tuple:
    """모델 이름 속 숫자(버전)가 큰 것이 앞에 오도록 하는 정렬 키."""
    return tuple(-int(n) for n in re.findall(r"\d+", mid)[:4])


_OPENAI_SKIP = re.compile(
    r"(embedding|whisper|tts|dall-e|image|audio|realtime|transcribe|moderation|search|instruct|davinci|babbage|"
    r"computer-use|codex|sora|chat-latest|:ft)|-\d{4}-\d{2}-\d{2}$|-\d{4}$", re.I)
_GEMINI_SKIP = re.compile(r"(embedding|tts|image|live|audio|robotics|computer-use|aqa|learnlm|imagen|veo)", re.I)


def _list_models(provider: str, key: str) -> list[str]:
    """이 키로 대화에 쓸 수 있는 모델 이름들(새 것 먼저). 토큰을 쓰지 않아 비용이 들지 않습니다."""
    to = 20
    if provider == "anthropic":
        r = _http(provider, "GET", f"{_base(provider)}/v1/models", key, params={"limit": 1000}, timeout=to)
    elif provider == "openai":
        r = _http(provider, "GET", f"{_base(provider)}/v1/models", key, timeout=to)
    else:
        r = _http(provider, "GET", f"{_base(provider)}/v1beta/models", key, params={"pageSize": 1000}, timeout=to)
    if r.status_code != 200:
        raise _map_error(provider, r)
    try:
        data = r.json()
    except ValueError:
        raise _AiError(502, "upstream_error", "모델 목록을 읽지 못했어요.")
    ids: list[str] = []
    if provider == "anthropic":
        ids = [str(m.get("id")) for m in data.get("data") or [] if str(m.get("id") or "").startswith("claude")]
    elif provider == "openai":
        ids = [str(m.get("id")) for m in data.get("data") or []
               if re.match(r"^(gpt-|o\d|chatgpt-)", str(m.get("id") or "")) and not _OPENAI_SKIP.search(str(m.get("id")))]
        ids.sort(key=lambda x: (_vkey(x), x))
    else:
        for m in data.get("models") or []:
            name = str(m.get("name") or "").removeprefix("models/")
            if name in config.AI_GEMINI_ALLOWED and "generateContent" in (m.get("supportedGenerationMethods") or []) and not _GEMINI_SKIP.search(name):
                ids.append(name)
        ids.sort(key=lambda x: (_vkey(x), x))
    seen: set[str] = set()
    return [i for i in ids if _MODEL_RE.match(i) and not (i in seen or seen.add(i))]


def _first(ids: list[str], *pats: str, skip: str = "") -> str:
    for p in pats:
        for i in ids:
            if re.search(p, i) and not (skip and re.search(skip, i)):
                return i
    return ""


def _pick(provider: str, ids: list[str]) -> tuple[str, str]:
    """목록에서 (기본 모델, 빠른 모델)을 자동으로 고릅니다. 사용자가 나중에 바꿀 수 있습니다."""
    if not ids:
        return "", ""
    if provider == "anthropic":
        d = _first(ids, "sonnet") or ids[0]
        q = _first(ids, "haiku") or d
    elif provider == "openai":
        d = _first(ids, r"^gpt-\d+(\.\d+)?$") or _first(ids, r"^gpt-", skip="mini|nano") or ids[0]
        q = _first(ids, "mini") or _first(ids, "nano") or d
    else:
        d = q = ids[0]   # 허용된 모델만 목록에 오므로 첫 번째를 씁니다
    return d, q


def _cached_models(uid: int, provider: str, key: str, refresh: bool = False) -> list[str]:
    hit = _models_cache.get((uid, provider))
    if hit and not refresh and time.monotonic() - hit[0] < _MODEL_CACHE_SEC:
        return hit[1]
    ids = _list_models(provider, key)
    _models_cache[(uid, provider)] = (time.monotonic(), ids)
    return ids


# ---------------------------------------------------------------- 대화 호출(서비스별)
def _call(provider: str, key: str, model: str, msgs: list[dict], images: list[dict], max_tokens: int, extra: dict | None = None) -> dict:
    """서비스별 형식으로 한 번 호출하고 {text, truncated, in, out}으로 통일해 돌려줍니다."""
    to = httpx.Timeout(config.AI_TIMEOUT_SEC, connect=10)
    if provider == "anthropic":
        api = [{"role": m["role"], "content": m["text"]} for m in msgs]
        if images:  # 이미지는 마지막 user 메시지에 붙입니다
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": i["media_type"], "data": i["data"]}} for i in images]
            api[-1]["content"] = [*blocks, {"type": "text", "text": msgs[-1]["text"]}]
        r = _http(provider, "POST", f"{_base(provider)}/v1/messages", key, body={"model": model, "max_tokens": max_tokens, "messages": api, **(extra or {})}, timeout=to)   # extra: 사고·노력 수준 같은 선택 설정(Claude 전용)
        if r.status_code != 200:
            raise _map_error(provider, r)
        d = _json(r)
        u = d.get("usage") or {}
        text = "".join(b.get("text", "") for b in (d.get("content") or []) if b.get("type") == "text")
        return {"text": text, "truncated": d.get("stop_reason") == "max_tokens", "in": u.get("input_tokens"), "out": u.get("output_tokens")}

    if provider == "openai":
        api = [{"role": m["role"], "content": m["text"]} for m in msgs]
        if images:
            parts = [{"type": "image_url", "image_url": {"url": f"data:{i['media_type']};base64,{i['data']}"}} for i in images]
            api[-1]["content"] = [*parts, {"type": "text", "text": msgs[-1]["text"]}]
        # 추론형 모델은 생각하는 데에도 토큰을 쓰므로 여유를 둡니다(max_completion_tokens는 새·옛 모델 모두 받습니다).
        r = _http(provider, "POST", f"{_base(provider)}/v1/chat/completions", key,
                  body={"model": model, "messages": api, "max_completion_tokens": max_tokens}, timeout=to)
        if r.status_code != 200:
            raise _map_error(provider, r)
        d = _json(r)
        ch = (d.get("choices") or [{}])[0]
        u = d.get("usage") or {}
        content = (ch.get("message") or {}).get("content")
        return {"text": content if isinstance(content, str) else "", "truncated": ch.get("finish_reason") == "length",
                "in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}

    # gemini
    contents = []
    for n, m in enumerate(msgs):
        parts: list[dict] = []
        if images and n == len(msgs) - 1:
            parts += [{"inline_data": {"mime_type": i["media_type"], "data": i["data"]}} for i in images]
        parts.append({"text": m["text"]})
        contents.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
    r = _http(provider, "POST", f"{_base(provider)}/v1beta/models/{model}:generateContent", key,
              body={"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}, timeout=to)
    if r.status_code != 200:
        raise _map_error(provider, r)
    d = _json(r)
    cands = d.get("candidates") or []
    if not cands:
        if (d.get("promptFeedback") or {}).get("blockReason"):
            raise _AiError(422, "blocked", "Gemini가 안전 필터 때문에 이 요청에 답하지 않았어요. 내용을 바꿔 다시 시도해 주세요.")
        raise _AiError(502, "upstream_error", "Gemini가 빈 답을 보냈어요. 다시 눌러 주세요.")
    c = cands[0]
    u = d.get("usageMetadata") or {}
    text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts") or [] if not p.get("thought"))
    # Gemini는 '생각'에 쓴 토큰(thoughtsTokenCount)을 출력 단가로 청구하면서 candidatesTokenCount에는 넣지 않으므로 더해서 기록합니다
    out_tokens = (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0)
    return {"text": text, "truncated": c.get("finishReason") == "MAX_TOKENS", "in": u.get("promptTokenCount"), "out": out_tokens if (u.get("candidatesTokenCount") is not None or u.get("thoughtsTokenCount") is not None) else None}


def _json(r) -> dict:
    try:
        d = r.json()
    except ValueError:
        raise _AiError(502, "upstream_error", "AI 서버의 답을 읽지 못했어요.")
    return d if isinstance(d, dict) else {}


# ---------------------------------------------------------------- 요청 정리
def _normalize(prompt: Any) -> list[dict]:
    """문자열 또는 메시지 목록을 user로 시작·끝, 역할 교대 형태로 정리합니다(세 서비스 모두 이 형태를 받습니다)."""
    raw: list[tuple[str, str]] = []
    items = [prompt] if isinstance(prompt, str) else (prompt if isinstance(prompt, list) else [])
    for m in items:
        if isinstance(m, str):
            raw.append(("user", m))
        elif isinstance(m, dict):
            role = "assistant" if m.get("role") == "assistant" else "user"
            c = m.get("content")
            raw.append((role, c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)))
    msgs: list[dict] = []
    for role, text in raw:
        text = (text or "").strip()
        if not text:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["text"] += "\n\n" + text
        else:
            msgs.append({"role": role, "text": text})
    if msgs and msgs[0]["role"] == "assistant":
        msgs.insert(0, {"role": "user", "text": "(대화 시작)"})
    if msgs and msgs[-1]["role"] == "assistant":
        msgs.append({"role": "user", "text": "계속해 주세요."})
    return msgs


def _parse_json(text: str) -> Any | None:
    t = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    t = re.sub(r"```\s*$", "", t)
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        return None
    a, b = min(starts), max(t.rfind("}"), t.rfind("]"))
    if b < a:
        return None
    try:
        return json.loads(t[a : b + 1])
    except ValueError:
        return None


class ImageIn(BaseModel):
    media_type: str
    data: str  # base64(머리글 없이)


class SampleIn(BaseModel):
    kind: Literal["json", "text"] = "text"
    prompt: str | list[Any]
    model_tier: Literal["quick", "default"] = "default"
    max_tokens: int | None = Field(default=None, ge=1)
    images: list[ImageIn] = []


class KeyIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=400)


class ModelsIn(BaseModel):
    model_default: str = Field(min_length=1, max_length=100)
    model_quick: str = Field(default="", max_length=100)  # 비우면 기본 모델과 같은 모델을 씁니다


class ActiveIn(BaseModel):
    provider: Literal["openai", "anthropic", "gemini"]


# ---------------------------------------------------------------- 엔드포인트
def _guard(db: DBSession, user: User, provider: str, *, need_crypto: bool = True) -> JSONResponse | None:
    if provider not in PROVIDERS:
        return _err(404, "unknown_provider", "알 수 없는 AI 서비스예요.")
    if _is_demo(db, user):
        return _err(403, "demo_ai_off", "데모 계정에서는 API 키를 등록할 수 없어요.")
    if need_crypto and not crypto.enabled():
        return _err(503, "not_configured", "서버에 암호화 키(APP_ENCRYPTION_KEY)가 설정되지 않아 키를 저장할 수 없어요. 서버 관리자에게 알려 주세요.")
    return None


def _decrypt(user: User, row: UserAiKey) -> str | JSONResponse:
    try:
        return crypto.decrypt(user.id, row.ciphertext)
    except crypto.CryptoError:
        return _err(424, "invalid_key", "저장된 키를 읽지 못했어요. 설정에서 키를 다시 등록해 주세요.")


@router.get("/status")
def status(user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    return _status(db, user)


@router.put("/providers/{provider}/key")
def put_key(provider: str, body: KeyIn, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    bad = _guard(db, user, provider)
    if bad:
        return bad
    info = PROVIDERS[provider]
    key = body.api_key.strip()
    if not info["key_re"].match(key):
        return _err(400, "bad_format", f"{info['label']} API 키 형식이 아니에요. {info['hint']} 처럼 생긴 키를 그대로 붙여 넣어 주세요.")
    if key_attempts.full(user.id):
        return _err(429, "rate_limited", "키 등록 시도가 너무 많아요. 10분 뒤 다시 해 주세요.")
    key_attempts.add(user.id)  # 서비스에 실제로 보내는 시도만 셉니다
    try:
        ids = _list_models(provider, key)
    except _AiError as e:
        if e.code == "invalid_key":
            return _err(400, "invalid_key", f"{info['vendor']}이(가) 이 키를 거절했어요. 키를 다시 확인해 주세요.")
        return _err(502, "upstream_error", f"키를 확인하지 못했어요({info['vendor']} 연결 문제). 잠시 뒤 다시 시도해 주세요.")
    if not ids:
        return _err(422, "no_models", f"이 키로 쓸 수 있는 {info['label']} 대화 모델을 찾지 못했어요. 결제·사용 권한을 확인해 주세요.")

    rows = _rows(db, user.id)
    row = rows.get(provider)
    d, q = _pick(provider, ids)
    token = crypto.encrypt(user.id, key)
    if row is None:
        db.add(UserAiKey(user_id=user.id, provider=provider, ciphertext=token, last4=key[-4:],
                         model_default=d, model_quick=q, active=not any(r.active for r in rows.values())))
    else:  # 키만 바꾸는 경우: 이전에 고른 모델이 새 목록에도 있으면 그대로 둡니다
        row.ciphertext, row.last4, row.updated_at = token, key[-4:], utcnow()
        if row.model_default not in ids:
            row.model_default = d
        if row.model_quick and row.model_quick not in ids:
            row.model_quick = q
    db.commit()
    _models_cache[(user.id, provider)] = (time.monotonic(), ids)
    return _status(db, user)


@router.delete("/providers/{provider}/key")
def delete_key(provider: str, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    if provider not in PROVIDERS:
        return _err(404, "unknown_provider", "알 수 없는 AI 서비스예요.")
    rows = _rows(db, user.id)
    row = rows.pop(provider, None)
    if row is not None:
        was_active = row.active
        db.delete(row)
        if was_active and rows:  # 쓰던 서비스를 지우면 남은 서비스 중 하나로 넘깁니다
            next(iter(rows.values())).active = True
        db.commit()
    _models_cache.pop((user.id, provider), None)
    return _status(db, user)


@router.get("/providers/{provider}/models")
def get_models(provider: str, refresh: bool = False, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    bad = _guard(db, user, provider)
    if bad:
        return bad
    row = _rows(db, user.id).get(provider)
    if row is None:
        return _err(409, "no_api_key", f"{PROVIDERS[provider]['label']} 키를 먼저 등록해 주세요.")
    key = _decrypt(user, row)
    if isinstance(key, JSONResponse):
        return key
    try:
        ids = _cached_models(user.id, provider, key, refresh)
    except _AiError as e:
        return _err(e.status, e.code, e.msg)
    finally:
        del key
    return {"models": ids, "model_default": row.model_default, "model_quick": row.model_quick}


@router.put("/providers/{provider}/models")
def put_models(provider: str, body: ModelsIn, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    bad = _guard(db, user, provider)
    if bad:
        return bad
    row = _rows(db, user.id).get(provider)
    if row is None:
        return _err(409, "no_api_key", f"{PROVIDERS[provider]['label']} 키를 먼저 등록해 주세요.")
    d, q = body.model_default.strip(), body.model_quick.strip()
    if not _MODEL_RE.match(d) or (q and not _MODEL_RE.match(q)):
        return _err(400, "bad_model", "모델 이름 형식이 아니에요.")
    key = _decrypt(user, row)
    if isinstance(key, JSONResponse):
        return key
    try:
        ids = _cached_models(user.id, provider, key)
        if d not in ids or (q and q not in ids):  # 목록이 오래됐을 수 있으니 한 번 새로 받아 확인합니다
            ids = _cached_models(user.id, provider, key, refresh=True)
    except _AiError as e:
        return _err(e.status, e.code, e.msg)
    finally:
        del key
    if d not in ids or (q and q not in ids):
        return _err(400, "bad_model", "이 키로 쓸 수 없는 모델이에요. 목록에서 골라 주세요.")
    row.model_default, row.model_quick = d, q
    db.commit()
    return _status(db, user)


@router.put("/active")
def put_active(body: ActiveIn, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    rows = _rows(db, user.id)
    if body.provider not in rows:
        return _err(409, "no_api_key", f"{PROVIDERS[body.provider]['label']} 키를 먼저 등록해 주세요.")
    for p, r in rows.items():
        r.active = p == body.provider
    db.commit()
    return _status(db, user)


@router.post("/sample")
def sample(body: SampleIn, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    if _is_demo(db, user):
        return _err(403, "demo_ai_off", "데모에서는 AI가 꺼져 있어요. ⚙️ 설정에서 AI를 '복사·붙여넣기'로 바꾸면 직접 써 볼 수 있어요.")
    if not crypto.enabled():
        return _err(503, "not_configured", "서버에 암호화 키가 설정되지 않아 AI를 쓸 수 없어요. 서버 관리자에게 알려 주세요.")
    row = _active_row(_rows(db, user.id))
    if row is None:
        return _err(
            409, "no_api_key",
            "AI 키가 아직 없어요. ⚙️ 설정 → AI 연결에서 ChatGPT·Claude·Gemini 중 하나의 키를 등록하거나, AI를 '복사·붙여넣기'로 바꿔 쓰세요.",
        )
    provider = row.provider
    model = (row.model_quick or row.model_default) if body.model_tier == "quick" else row.model_default
    if provider == "gemini" and model not in config.AI_GEMINI_ALLOWED and config.AI_GEMINI_ALLOWED:
        model = config.AI_GEMINI_ALLOWED[0]   # 예전에 저장된(지금은 허용 안 되는) 모델은 허용된 모델로 대체
    if not model and provider == "anthropic":
        model = config.AI_MODEL_QUICK if body.model_tier == "quick" else config.AI_MODEL_DEFAULT
    if not model:
        return _err(409, "no_model", f"{PROVIDERS[provider]['label']} 모델이 아직 정해지지 않았어요. ⚙️ 설정에서 모델을 골라 주세요.")

    msgs = _normalize(body.prompt)
    if not msgs:
        return _err(400, "empty_prompt", "보낼 내용이 비어 있어요.")
    if sum(len(m["text"]) for m in msgs) > config.AI_MAX_PROMPT_CHARS:
        return _err(413, "too_large", "보내는 글이 너무 길어요. 줄여서 다시 시도해 주세요.")

    if len(body.images) > config.AI_MAX_IMAGES:
        return _err(400, "too_many_images", f"이미지는 한 번에 {config.AI_MAX_IMAGES}장까지 읽을 수 있어요.")
    images = []
    for im in body.images:
        if im.media_type not in _IMG_TYPES:
            return _err(400, "bad_image", "지원하지 않는 이미지 형식이에요(PNG·JPEG·WEBP·GIF).")
        try:
            raw = base64.b64decode(im.data, validate=True)
        except (binascii.Error, ValueError):
            return _err(400, "bad_image", "이미지를 읽지 못했어요.")
        if len(raw) > config.AI_MAX_IMAGE_BYTES:
            return _err(413, "too_large", "이미지가 너무 커요(장당 4MB까지).")
        images.append({"media_type": im.media_type, "data": im.data})

    per_minute.limit = config.AI_PER_MIN_LIMIT
    if per_minute.full(user.id):
        return _err(429, "rate_limited", "AI 요청이 잠시 많아요. 1분 뒤 다시 눌러 주세요.")
    used = _usage(db, user.id)
    if used and used.calls >= config.AI_DAILY_LIMIT:
        return _err(429, "daily_limit", f"오늘 AI 사용 한도({config.AI_DAILY_LIMIT}회)를 다 썼어요. 내일 다시 쓸 수 있어요.")

    key = _decrypt(user, row)
    if isinstance(key, JSONResponse):
        return key
    default_max = 2048 if body.model_tier == "quick" else 6000
    max_tokens = min(body.max_tokens or default_max, config.AI_MAX_TOKENS_CAP)
    if provider != "anthropic":  # ChatGPT·Gemini의 추론형 모델은 생각하는 데에도 토큰을 써서, 너무 작으면 답이 비어 버립니다
        max_tokens = max(max_tokens, 4096)

    per_minute.add(user.id)
    try:
        out = _call(provider, key, model, msgs, images, max_tokens)
    except _AiError as e:
        return _err(e.status, e.code, e.msg)
    finally:
        del key  # 복호화한 키를 오래 들고 있지 않습니다

    u = _usage(db, user.id)
    if u is None:
        u = AiUsage(user_id=user.id, day=_today(), calls=0, input_tokens=0, output_tokens=0)
        db.add(u)
    u.calls += 1
    u.input_tokens += int(out["in"] or 0)
    u.output_tokens += int(out["out"] or 0)
    db.commit()

    text, truncated = out["text"], out["truncated"]
    parsed = None
    if body.kind == "json":
        parsed = _parse_json(text)
        if parsed is None:
            msg = "AI의 답이 길어서 중간에 잘렸어요. 다시 눌러 주세요." if truncated else "AI가 JSON 형식이 아닌 답을 보냈어요. 다시 눌러 주세요."
            return _err(422, "truncated" if truncated else "bad_json", msg)
    return {"text": text, "json": parsed, "truncated": truncated, "model": model, "provider": provider}
