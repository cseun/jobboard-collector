"""서버가 스스로 공고의 빈칸(필수·우대·급여·전형 절차·근무 위치)을 채웁니다. 기본은 미리보기, --apply 를 붙여야 저장합니다.

    docker compose exec -T api python -m scripts.fill_gaps                  # 미리보기: 어떤 공고를 어떤 자료로 채울지만 보여 줌(AI 호출 없음)
    docker compose exec -T api python -m scripts.fill_gaps --apply --max 10

순서(비용이 싼 것부터):
  1) 저장된 본문(`원문 본문`)이 있으면 규칙 파서(비용 0)로 필수·우대를 먼저 나눔
  2) 그래도 빈 칸이 있으면 본문을 Claude(Sonnet)에게 보내 빈 칸만 JSON으로 받음 — 본문에 없는 내용은 비워 두게 시킴(추측 금지)
  3) 본문이 없으면 사이트에서 다시 읽음(사람인 텍스트 공고·잡코리아). 이미지 공고는 수집기의 AI 이미지 읽기가 맡으므로 여기서는 건너뜀. 원티드는 API가 구조로 주므로 건너뜀
- 하루 최대 --max 건(기본 10). 공고당 최대 2번 시도(`채움 시도`)하고, 그래도 못 찾으면 빈칸으로 둠.
- 기본은 빈칸만 채움(이미 값이 있으면 건드리지 않음). 채운 곳은 `채움 기록`에 '서버 AI 모델 날짜'로 남고, 실행 기록(collect_runs, 모드 '빈칸 채우기')에도 토큰·비용이 남음.
- 등록일은 AI로 추측하지 않으므로 기본 대상이 아닙니다.
"""
import argparse
import json
import sys
from datetime import date
from urllib.parse import urljoin

import httpx
from sqlalchemy import select

from app import gaps
from app.dates import today_kst
from app.db import SessionLocal, init_db
from app.models import CollectRun, Job, utcnow
from app.routers import ai as ai_mod
from scripts import collect as co
from scripts import sync_sheet as ss
from scripts.collect_sources import jobkorea_parse_detail, jobkorea_parse_frame
from scripts.text_requirements import parse_requirements

FIELDS = ("필수", "우대", "급여(공고)", "전형 절차", "근무 위치")
MAX_PER_DAY = 10
MAX_TRIES = 2
TEXT_MIN = 150          # 이보다 짧은 본문은 자료로 쓰지 않음
TEXT_MAX = 8000         # AI에 보내는 본문 길이 한도
SETTINGS = {"max_tokens": 1500, "thinking": "기본(Sonnet은 끌 수 없음)", "effort": "기본(medium)", "프롬프트": "빈칸만 추출(추측 금지)"}

PROMPT = (
    "아래는 채용공고 본문입니다. 요청한 항목만 본문에 적힌 내용 그대로 뽑아 JSON 하나로만 답하세요. 설명 문장은 쓰지 마세요.\n"
    "규칙:\n"
    "1) 본문에 없는 항목은 빈 문자열(\"\")로 두세요. 추측하거나 비슷한 내용으로 채우지 마세요.\n"
    "2) \"필수\"는 자격요건·지원자격·필수 항목만, \"우대\"는 우대사항만 넣으세요. 업무 내용·복지를 섞지 마세요. 우대 항목이 본문에 없다고 분명하면 \"없음\"이라고 쓰세요.\n"
    "3) 여러 항목은 줄마다 \"- \"로 시작하는 한 문자열로 쓰세요. 원문 표현을 바꾸지 마세요.\n"
    "4) \"급여(공고)\"는 본문에 적힌 연봉·급여 문구만, \"전형 절차\"는 전형 단계만, \"근무 위치\"는 근무지 주소·지역만 적으세요.\n"
    "요청 항목: {fields}\n"
    "{{{keys}}}\n\n=== 본문 ===\n{text}"
)


def pick_targets(db, fields=FIELDS, ref: date | None = None, limit: int = MAX_PER_DAY) -> list[Job]:
    """채울 공고: 접수 중 · 대상 칸이 비어 있음 · 시도 2번 미만 · 오늘 이미 시도한 것은 제외 · 최신 공고 먼저. 이미지 공고·원티드는 제외."""
    ref = ref or today_kst()
    out = []
    for j in db.execute(select(Job).order_by(Job.id.desc())).scalars():
        raw = j.raw or {}
        if not gaps.is_active(j) or not gaps.blanks_of(raw, fields):
            continue
        if raw.get("채움 시도", 0) >= MAX_TRIES or raw.get("채움 시도일") == ref.isoformat():
            continue
        kind = ss.canon(j.source_key).split(":")[0]
        if raw.get("이미지 공고") is True or kind == "wanted" or kind not in ("saramin", "jobkorea"):
            continue
        out.append(j)
    return out[:limit]


def source_text(j: Job, fetch) -> tuple[str, str]:
    """(본문, 출처 설명). 저장된 본문이 충분하면 그것을 쓰고, 아니면 사이트에서 다시 읽음. 못 읽으면 ('', 이유)."""
    raw = j.raw or {}
    saved = (raw.get("원문 본문") or "").strip()
    if len(saved) >= TEXT_MIN:
        return saved, "저장된 본문"
    kind, rid = ss.canon(j.source_key).split(":", 1)
    if kind == "saramin":
        info = co.parse_detail(fetch(co.SOURCES["saramin"].detail_url(rid)))
        return (info["text"], "사람인 상세") if not info["is_image"] and len(info["text"]) >= TEXT_MIN else ("", "본문이 이미지이거나 너무 짧음")
    url = co.SOURCES["jobkorea"].view(rid)
    d = jobkorea_parse_detail(fetch(url))
    if len(d["text"]) < TEXT_MIN * 3 and d["iframe"]:
        d = jobkorea_parse_frame(fetch(urljoin(url, d["iframe"])))
    return (d["text"], "잡코리아 상세") if len(d["text"]) >= TEXT_MIN else ("", "본문을 읽지 못함(본문이 따로 로드되는 형식일 수 있음)")


def ask_ai(text: str, blanks: list[str], key: str, model: str = co.AI_MODEL) -> dict:
    from scripts.test_image_read import estimate_cost
    prompt = PROMPT.format(fields=", ".join(blanks), keys=", ".join(f'"{b}": ""' for b in blanks), text=text[:TEXT_MAX])
    res = ai_mod._call("anthropic", key, model, [{"role": "user", "text": prompt}], [], SETTINGS["max_tokens"])
    return {"data": ai_mod._parse_json(res["text"]) or {}, "model": model, "tokens_in": res["in"] or 0, "tokens_out": res["out"] or 0,
            "cost_usd": estimate_cost("anthropic", model, res["in"], res["out"]) or 0.0}


def fill_one(j: Job, text: str, blanks: list[str], key, ai, ref: date, use: dict) -> dict:
    """한 공고 채우기. 규칙 파서 → (남은 칸이 있으면) AI. 반환: {칸: 채운 방법}"""
    got: dict[str, str] = {}
    by: dict[str, str] = {}
    rule = parse_requirements(text)
    for k, v in rule.items():
        if k in blanks and v:
            got[k], by[k] = v, "규칙 파서"
    left = [b for b in blanks if b not in got]
    if left:
        r = ai(text, left, key)
        use["model"] = r.get("model") or use["model"]
        use["tokens_in"] += r.get("tokens_in", 0)
        use["tokens_out"] += r.get("tokens_out", 0)
        use["cost_usd"] += r.get("cost_usd") or 0.0
        use["calls"] += 1
        for k in left:
            v = r["data"].get(k)
            v = "\n".join(f"- {x}" for x in v) if isinstance(v, list) else str(v or "").strip()
            if k == "우대" and v == "없음":
                v = "—"                              # 보드의 '없음' 표기(빈칸으로 치지 않음)
            if v:
                got[k], by[k] = v, f"서버 AI {use['model'] or co.AI_MODEL}"
    applied = []
    for k, v in got.items():
        res = gaps.apply_fill(j, {k: v}, by=by[k], today=ref)
        applied += res["applied"]
    return {k: by[k] for k in applied}


def run(apply: bool = False, max_n: int = MAX_PER_DAY, fields=FIELDS, email: str | None = None, fetch=None, ai=None, ref: date | None = None, say=print) -> dict:
    ref = ref or today_kst()
    init_db()
    fetch = fetch or co.Fetcher().get
    use = {"model": None, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0}
    out = {"mode": "저장함" if apply else "미리보기(저장 안 함)", "대상": 0, "채운 공고": 0, "채운 칸": {}, "건너뜀": [], "AI 사용": use}
    with SessionLocal() as db:
        targets = pick_targets(db, fields, ref, max_n)
        out["대상"] = len(targets)
        if not apply:
            out["대상 예시"] = [f"{j.company} — {j.title} (빈칸: {', '.join(gaps.blanks_of(j.raw, fields))})" for j in targets]
            say(f"→ 채울 공고 {len(targets)}건(하루 최대 {max_n}). 저장하려면 --apply")
            return out
        rec = CollectRun(day=ref.isoformat(), mode="빈칸 채우기", status="실행 중", options={"max": max_n, "fields": list(fields)})
        db.add(rec)
        db.commit()
        key, status, requests = None, "완료", 0
        try:
            for j in targets:
                raw = dict(j.raw or {})
                raw["채움 시도"], raw["채움 시도일"] = raw.get("채움 시도", 0) + 1, ref.isoformat()   # 실패해도 횟수를 남겨 무한 반복을 막음
                j.raw = raw
                try:
                    text, why = source_text(j, fetch)
                    requests += 0 if why == "저장된 본문" else 1
                    if not text:
                        out["건너뜀"].append(f"{j.company}: {why}")
                        continue
                    blanks = gaps.blanks_of(j.raw, fields)
                    key = key or (ai and "test") or co._ai_key(db, email)
                    done = fill_one(j, text, blanks, key, ai or ask_ai, ref, use)
                except co.Blocked as e:
                    status = "차단으로 중단"
                    rec.blocked = str(e)[:200]
                    out["중단"] = str(e)
                    break
                except Exception as e:                       # 한 건이 실패해도 나머지는 계속(AI 오류·네트워크 오류 등)
                    out["건너뜀"].append(f"{j.company}: {type(e).__name__}: {e}"[:200])
                    continue
                if done:
                    out["채운 공고"] += 1
                    for k in done:
                        out["채운 칸"][k] = out["채운 칸"].get(k, 0) + 1
                else:
                    out["건너뜀"].append(f"{j.company}: 본문에서 찾지 못함")
                db.commit()
        finally:
            rec.finished_at, rec.status, rec.requests, rec.detail_requests = utcnow(), status, requests, requests
            rec.updated, rec.ai_reads = out["채운 공고"], use["calls"]
            rec.ai_model, rec.ai_settings = (use["model"] or None), (SETTINGS if use["calls"] else None)
            rec.input_tokens, rec.output_tokens, rec.cost_usd = use["tokens_in"], use["tokens_out"], round(use["cost_usd"], 4)
            rec.notes = out["건너뜀"][:20] or None
            db.commit()
    say(f"→ 대상 {out['대상']}건 · 채움 {out['채운 공고']}건 {out['채운 칸']} · AI {use['calls']}번 · 토큰 {use['tokens_in']}/{use['tokens_out']} · ${use['cost_usd']:.4f}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="공고 빈칸 자동 채우기(기본: 미리보기)")
    ap.add_argument("--apply", action="store_true", help="실제로 저장(없으면 미리보기)")
    ap.add_argument("--max", type=int, default=MAX_PER_DAY, help=f"하루 최대 공고 수(기본 {MAX_PER_DAY})")
    ap.add_argument("--fields", help=f"채울 칸(쉼표). 기본: {','.join(FIELDS)}")
    ap.add_argument("--email", help="AI 키를 가진 계정 이메일(없으면 첫 관리자)")
    ap.add_argument("--sleep", type=float, nargs=2, default=(3.0, 5.0), metavar=("MIN", "MAX"))
    a = ap.parse_args(argv)
    fields = tuple(f.strip() for f in a.fields.split(",") if f.strip()) if a.fields else FIELDS
    bad = [f for f in fields if f not in gaps.GAP_FIELDS]
    if bad:
        ap.error(f"채울 수 없는 칸: {', '.join(bad)} (가능: {', '.join(gaps.GAP_FIELDS)})")
    f = co.Fetcher(sleep=tuple(a.sleep), max_requests=100)
    res = run(apply=a.apply, max_n=a.max, fields=fields, email=a.email, fetch=f.get)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 2 if res.get("중단") else 0


if __name__ == "__main__":
    sys.exit(main())
