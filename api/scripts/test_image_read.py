"""이미지로만 올라온 공고를 AI로 읽어 보는 테스트. 아무것도 저장하지 않고, 읽은 내용과 토큰·비용 추정만 출력합니다.

    docker compose exec -T api python -m scripts.test_image_read URL [URL ...] [--list-models] [--email 내@메일] [--provider gemini|anthropic|openai] [--model 모델ID] [--slice 900] [--jpeg 85]

- URL은 공고 상세에 들어 있는 **이미지 주소**입니다(공고 페이지가 아니라 .png/.jpg 주소).
- 키는 관리자 계정(--email 생략 시 첫 관리자)에 등록해 둔 것을 씁니다. 활성 서비스를 쓰고, --provider로 바꿔 볼 수 있어요.
- 서버가 이미지를 직접 내려받는 것도 이 스크립트가 처음 확인합니다(상태 코드·크기·소요 시간을 출력).
- 세로로 긴 이미지는 글씨가 뭉개지지 않게 --slice 픽셀 높이로 잘라서 한 번에 보냅니다(Pillow가 있을 때).
"""
import argparse
import base64
import io
import json
import re
import sys
import time

import httpx
from sqlalchemy import select

from app import config, crypto
from app.db import SessionLocal
from app.models import User
from app.routers import ai as ai_mod

PROMPT = (
    "이 이미지는 채용공고의 상세 내용입니다(세로로 긴 한 장을 위에서부터 잘라 순서대로 보냈습니다). "
    "보이는 글자만 읽어서 아래 JSON 하나로만 답하세요. 설명 문장은 쓰지 마세요.\n"
    "규칙:\n"
    "1) 이미지에 적힌 구역 제목을 그대로 따라 나누세요. '필수 자격 요건'의 항목만 \"필수\"에, '선호 자격 요건'(또는 우대 사항)의 항목만 \"우대\"에 넣고, "
    "'필요역량'·'요구 역량' 같은 별도 구역은 \"필요역량\"에 넣으세요. 구역이 달라도 한데 합치지 마세요.\n"
    "2) 위 어느 칸에도 맞지 않는 글(병역·장애인·보훈 우대 안내, 유의사항, 회사 사정에 따른 일정 변경 문구 등)은 버리지 말고 \"기타안내\"에 그대로 옮기세요.\n"
    "3) 이미지에 없는 내용은 빈 값으로 두고, 비슷해 보이는 글자를 추측해 고치지 마세요. 읽기 어려운 글자는 \"읽지못한부분\"에 어느 구역의 어떤 부분인지 적으세요.\n"
    '{"모집분야": "", "담당업무": [], "필수": [], "우대": [], "필요역량": [], "학력": "", "경력": "", "근무조건": "", "근무지": "", '
    '"급여": "", "복지": [], "전형절차": "", "제출서류": [], "접수기간": "", "기타안내": [], "읽지못한부분": []}'
)

PROOF_PROMPT = (
    "아래는 채용공고 이미지를 글자로 옮긴 결과(JSON)입니다. 글자 인식 오류로 보이는 단어만 찾아 고치세요.\n"
    "규칙:\n"
    "1) 한국어 공고에서 실제로 쓰이는 말로 바로잡을 수 있는 것만 \"정정\"에 넣으세요(존재하지 않는 단어, 흔한 말과 한 글자만 다른 단어 등).\n"
    "2) 확실하지 않거나 이미지에 그대로 적혀 있을 수 있는 표현은 고치지 말고 \"의심\"에만 적으세요.\n"
    "3) 항목을 추가·삭제·재분류하지 말고, 뜻이 바뀌게 고치지 마세요. 원문이 맞아 보이면 아무것도 고치지 마세요.\n"
    "4) 다음 JSON 하나로만 답하세요. 설명 문장은 쓰지 마세요.\n"
    '{"정정": [{"원문": "", "수정": "", "이유": ""}], "의심": [{"원문": "", "이유": ""}]}\n\n'
)


def apply_corrections(data, fixes: list[dict]):
    """JSON 안의 글자에서 정정 목록의 원문을 수정으로 바꿉니다. (바뀐 결과, 적용한 정정, 못 찾은 정정)"""
    applied, missed = [], []
    clean = [f for f in fixes if isinstance(f, dict) and f.get("원문") and f.get("수정") and f["원문"] != f["수정"]]
    hit = set()

    def walk(x):
        if isinstance(x, str):
            for i, f in enumerate(clean):
                if f["원문"] in x:
                    x = x.replace(f["원문"], f["수정"])
                    hit.add(i)
            return x
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        return x

    out = walk(data)
    for i, f in enumerate(clean):
        (applied if i in hit else missed).append(f)
    return out, applied, missed


# 2026-11 기준 공개 단가(USD / 100만 토큰, 입력·출력). 바뀔 수 있어 참고용입니다. 목록에 없는 모델은 토큰 수만 보여 줍니다.
PRICES = [
    ("anthropic", r"sonnet", 2.0, 10.0),
    ("anthropic", r"opus", 4.0, 20.0),
    ("anthropic", r"haiku-5", 0.10, 0.50),                 # 프롬프트 100K 이하 기준
    ("openai", r"gpt-6-luna$", 0.10, 0.50),
    ("openai", r"gpt-6-sol$", 2.0, 10.0),
    ("gemini", r"3\.8-flash(?!-lite)", 0.75, 3.75),   # 2026-12-31까지 도입 가격, 2027-01-01부터 1.50 / 7.50
    ("gemini", r"3\.5-flash-lite", 0.30, 2.50),
]


def _show_raw_errors(say) -> None:
    """서비스가 돌려준 실제 상태 코드와 원문 오류 메시지를 함께 출력합니다(키는 출력되지 않음)."""
    orig = getattr(ai_mod._map_error, "_orig", ai_mod._map_error)     # 여러 번 불려도 겹쳐 감싸지 않음

    def wrapped(provider, r):
        try:
            em, tag = ai_mod._err_text(r)
        except Exception:
            em, tag = "", ""
        say(f"  [진단] {provider} 응답 HTTP {r.status_code} · {tag} · {em[:300]}")
        return orig(provider, r)
    wrapped._orig = orig
    ai_mod._map_error = wrapped


def estimate_cost(provider: str, model: str, tin: int | None, tout: int | None) -> float | None:
    for prov, pat, pin, pout in PRICES:
        if prov == provider and re.search(pat, model or "", re.I):
            return ((tin or 0) * pin + (tout or 0) * pout) / 1_000_000
    return None


def fetch_image(url: str) -> tuple[bytes, str, dict]:
    """이미지를 서버에서 직접 내려받습니다. (바이트, 형식, 확인용 정보)"""
    t0 = time.monotonic()
    r = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "jobboard-personal/1.0"})
    info = {"status": r.status_code, "bytes": len(r.content), "content_type": r.headers.get("content-type", ""), "sec": round(time.monotonic() - t0, 2)}
    if r.status_code != 200:
        raise RuntimeError(f"이미지를 내려받지 못했어요(상태 {r.status_code}).")
    media = info["content_type"].split(";")[0].strip().lower()
    if media not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        raise RuntimeError(f"이미지가 아닙니다({media or '형식 없음'}). 공고 페이지 주소가 아니라 이미지 주소를 넣어 주세요.")
    return r.content, media, info


def slice_image(data: bytes, media: str, max_h: int, overlap: int = 40, jpeg: int = 0) -> tuple[list[tuple[bytes, str]], str]:
    """세로로 긴 이미지를 비슷한 높이로 자릅니다(조각마다 위아래 overlap픽셀 겹침). (조각 목록, 메모)"""
    try:
        from PIL import Image
    except ImportError:
        return [(data, media)], "Pillow가 없어 자르지 않고 한 장으로 보냈어요"
    im = Image.open(io.BytesIO(data))
    w, h = im.size
    def enc(img):
        buf = io.BytesIO()
        if jpeg:
            img.convert("RGB").save(buf, "JPEG", quality=jpeg)
            return buf.getvalue(), "image/jpeg"
        img.convert("RGB").save(buf, "PNG")
        return buf.getvalue(), "image/png"

    if max_h <= 0 or h <= max_h:
        if jpeg:
            return [enc(im)], f"자르지 않음({w}×{h}), JPEG {jpeg}로 변환"
        return [(data, media)], f"자르지 않음({w}×{h})"
    n = -(-h // max_h)
    step = -(-h // n)
    out = []
    for i in range(n):
        top, bot = max(0, i * step - (overlap if i else 0)), min(h, (i + 1) * step + (overlap if i < n - 1 else 0))
        out.append(enc(im.crop((0, top, w, bot))))
    return out, f"{w}×{h}를 {n}조각으로 잘랐어요" + (f" (JPEG {jpeg})" if jpeg else "")


def run(urls: list[str], email: str | None = None, provider: str | None = None, model: str | None = None, slice_h: int = 900, say=print, list_models: bool = False, jpeg: int = 0, effort: str = "", no_thinking: bool = False, proofread: bool = False, proof_model: str = "") -> dict:
    with SessionLocal() as db:
        q = select(User).where(User.email == email.strip().lower()) if email else select(User).where(User.is_admin.is_(True)).order_by(User.id)
        user = db.execute(q).scalars().first()
        if user is None:
            raise SystemExit("계정을 찾지 못했어요. --email로 관리자 이메일을 알려 주세요.")
        rows = ai_mod._rows(db, user.id)
        row = rows.get(provider) if provider else ai_mod._active_row(rows)
        if row is None:
            raise SystemExit(f"{user.email} 계정에 {'해당 서비스의 ' if provider else ''}AI 키가 없어요. 보드 ⚙️ 설정 → AI 연결에서 먼저 등록해 주세요.")
        try:
            key = crypto.decrypt(user.id, row.ciphertext)
        except crypto.CryptoError as e:
            raise SystemExit(f"키를 풀지 못했어요: {e}")
        prov = row.provider
        mdl = model or row.model_default
        if not model and prov == "gemini" and config.AI_GEMINI_ALLOWED and mdl not in config.AI_GEMINI_ALLOWED:
            mdl = config.AI_GEMINI_ALLOWED[0]     # 저장된 모델이 허용 목록에 없으면 허용된 모델로
        if not mdl:
            mdl = ai_mod._pick(prov, ai_mod._list_models(prov, key))[0]
    say(f"계정 {user.email} · 서비스 {prov} · 모델 {mdl}")
    _show_raw_errors(say)
    if list_models:
        say("--- 이 키로 쓸 수 있는 모델 ---")
        try:
            for m in ai_mod._list_models(prov, key):
                say(f"  {m}")
        finally:
            del key
        return {}

    images, fetched, notes = [], [], []
    for u in urls:
        data, media, info = fetch_image(u)
        fetched.append(info)
        say(f"  내려받기: 상태 {info['status']} · {info['bytes']:,}바이트 · {info['content_type']} · {info['sec']}초")
        parts, note = slice_image(data, media, slice_h, jpeg=jpeg)
        notes.append(note)
        say(f"  {note} · 보내는 크기 합계 {sum(len(d) for d, _ in parts):,}바이트")
        for d, m in parts:
            if len(d) > config.AI_MAX_IMAGE_BYTES:
                raise RuntimeError("조각 하나가 4MB를 넘어요. --slice 값을 줄여 보세요.")
            images.append({"media_type": m, "data": base64.b64encode(d).decode()})

    extra: dict = {}
    if effort:
        extra["output_config"] = {"effort": effort}
    if no_thinking:
        extra["thinking"] = {"type": "disabled"}
    if extra:
        if prov != "anthropic":
            say("  ⚠️ --effort·--no-thinking은 Claude에서만 적용돼요(이번 호출에는 무시)")
            extra = {}
        else:
            say(f"  Claude 설정: {json.dumps(extra, ensure_ascii=False)}")
    t0 = time.monotonic()
    try:
        kw = {"extra": extra} if extra else {}
        res = ai_mod._call(prov, key, mdl, [{"role": "user", "text": PROMPT}], images, 4096, **kw)
    finally:
        del key
    sec = round(time.monotonic() - t0, 2)
    parsed = ai_mod._parse_json(res["text"])
    cost = estimate_cost(prov, mdl, res["in"], res["out"])
    out = {"provider": prov, "model": mdl, "images": len(images), "tokens_in": res["in"], "tokens_out": res["out"], "truncated": res["truncated"],
           "cost_usd": cost, "sec": sec, "fetched": fetched, "parsed": parsed, "raw_text": res["text"]}
    say("--- 읽은 내용 ---")
    say(json.dumps(parsed, ensure_ascii=False, indent=2) if parsed is not None else "(JSON으로 읽지 못함) " + res["text"][:600])
    say("--- 사용량 ---")
    say(f"보낸 이미지 {len(images)}장 · 입력 {res['in']} 토큰 · 출력 {res['out']} 토큰 · {sec}초" + (" · ⚠️ 출력이 잘렸어요" if res["truncated"] else ""))
    say(f"비용 추정: ${cost:.4f}" if cost is not None else "비용 추정: 이 모델 단가가 목록에 없어요(토큰 수로 직접 계산하세요)")

    if proofread and parsed is not None:
        pm = proof_model or mdl
        say(f"--- 2단계: 이상한 단어 교정 (글자만 보내요, 이미지 없음) · 모델 {pm} ---")
        t1 = time.monotonic()
        # 키는 위에서 지웠으므로 다시 풀어 씁니다
        with SessionLocal() as db:
            row2 = ai_mod._rows(db, user.id).get(prov)
            key2 = crypto.decrypt(user.id, row2.ciphertext)
        try:
            r2 = ai_mod._call(prov, key2, pm, [{"role": "user", "text": PROOF_PROMPT + json.dumps(parsed, ensure_ascii=False)}], [], 4096)
        finally:
            del key2
        p2 = ai_mod._parse_json(r2["text"]) or {}
        fixed, applied, missed = apply_corrections(parsed, p2.get("정정") or [])
        c2 = estimate_cost(prov, pm, r2["in"], r2["out"])
        say(f"정정 {len(applied)}건 적용" + (f" · 본문에서 못 찾은 정정 {len(missed)}건" if missed else ""))
        for f in applied:
            say(f"  ✏️ {f['원문']} → {f['수정']}   ({f.get('이유', '')})")
        for f in missed:
            say(f"  ⚠️ 못 찾음: {f['원문']} → {f['수정']}")
        for f in p2.get("의심") or []:
            say(f"  ❓ 의심만 함(고치지 않음): {f.get('원문')}   ({f.get('이유', '')})")
        say(f"2단계 사용량: 입력 {r2['in']} · 출력 {r2['out']} 토큰 · {round(time.monotonic() - t1, 2)}초" + (f" · 비용 ${c2:.4f}" if c2 is not None else ""))
        if cost is not None and c2 is not None:
            say(f"1+2단계 합계 비용: ${cost + c2:.4f}")
        out.update({"proof": {"applied": applied, "missed": missed, "suspect": p2.get("의심") or [], "tokens_in": r2["in"], "tokens_out": r2["out"], "cost_usd": c2}, "fixed": fixed})
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="이미지 공고를 AI로 읽어 보는 테스트(저장 안 함)")
    ap.add_argument("urls", nargs="*", help="공고 상세 이미지 주소")
    ap.add_argument("--email", help="키를 등록한 관리자 이메일(생략하면 첫 관리자)")
    ap.add_argument("--provider", choices=["anthropic", "openai", "gemini"], help="쓸 서비스(생략하면 활성 서비스)")
    ap.add_argument("--model", help="모델 ID(생략하면 등록된 기본 모델)")
    ap.add_argument("--slice", type=int, default=900, help="이 높이(px)보다 긴 이미지는 잘라서 보냄(0이면 자르지 않음)")
    ap.add_argument("--proofread", action="store_true", help="읽은 뒤 글자만 보내 이상한 단어를 한 번 더 교정(이미지는 다시 안 보냄)")
    ap.add_argument("--proof-model", default="", help="교정 단계에 쓸 모델(생략하면 읽기와 같은 모델)")
    ap.add_argument("--effort", choices=["low", "medium", "high"], help="Claude 노력 수준(낮을수록 생각을 덜 해서 출력 토큰·비용이 줄어듦)")
    ap.add_argument("--no-thinking", action="store_true", help="Claude 사고를 끔(Haiku 5.5는 effort가 high 이하일 때만 가능, Sonnet 5.5는 끌 수 없음)")
    ap.add_argument("--jpeg", type=int, default=0, help="JPEG 품질(1~95)로 변환해서 전송(0이면 PNG 그대로)")
    ap.add_argument("--list-models", action="store_true", help="이 키로 쓸 수 있는 모델 이름만 출력")
    a = ap.parse_args(argv)
    if not a.urls and not a.list_models:
        ap.error("이미지 주소를 넣거나 --list-models를 쓰세요")
    try:
        run(a.urls, a.email, a.provider, a.model, a.slice, list_models=a.list_models, jpeg=a.jpeg, effort=a.effort or "", no_thinking=a.no_thinking, proofread=a.proofread, proof_model=a.proof_model)
    except (RuntimeError, ai_mod._AiError) as e:
        sys.exit(f"✗ {getattr(e, 'msg', None) or e}")


if __name__ == "__main__":
    main()
