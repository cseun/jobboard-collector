"""보드에서 '🧰 지원 준비'를 요청한 공고를 서버가 직접 정리합니다. 기본은 미리보기, --apply 를 붙여야 저장합니다.

    docker compose exec -T api python -m scripts.prep_jobs                  # 미리보기: 처리할 요청 목록만 보여 줌(AI 호출 없음)
    docker compose exec -T api python -m scripts.prep_jobs --apply --max 3

흐름
  1) 보드 '📨 요청 기록'이 남긴 요청(문서 저장소 prep/<키>, 상태 '요청됨')을 읽음
  2) 요청한 공고를 찾아 본문(저장본 또는 사이트에서 다시 읽기) + 회사 정보(소개·사업·규모) + 최근 뉴스 제목을 모음
  3) Claude(Sonnet)가 본문에 적힌 것만 근거로 직무 정보·지원 방법·자소서 문항·준비 팁을 JSON으로 정리(추측 금지)
  4) 서류 준비 자료(admin:docs)에 **비어 있는 칸만** 채움(직접 쓴 내용은 덮어쓰지 않음) · 준비 목록(prep)에 '자동 정리' 표시
  5) 요청 문서의 상태를 '서버 처리 완료'(또는 실패 사유)로 바꾸고, 서버가 못 하는 일(로그인 필요한 후기·모의면접 질문)을 남김
- 하루 최대 --max 건(기본 3), 요청당 AI 1번. 실패하면 사유를 남기고 최대 2번까지만 다시 시도.
- 본문에 없는 내용은 비워 둠. 합격 자소서 예시·면접 후기(캐치·잡플래닛·채팅방)·모의면접 질문은 서버가 만들지 않음 → '남은 일'로 표시.
"""
import argparse
import json
import re
import sys
from datetime import date

from sqlalchemy import select

from app import gaps
from app.dates import today_kst
from app.db import SessionLocal, init_db
from app.models import CollectRun, Document, Job, SharedData, utcnow
from app.routers import ai as ai_mod
from scripts import collect as co
from scripts import sync_sheet as ss
from scripts.enrich_companies import short_name
from scripts.fill_gaps import TEXT_MIN, source_text

MAX_PER_DAY = 3
MAX_TRIES = 2
TEXT_MAX = 9000
SETTINGS = {"max_tokens": 3000, "thinking": "기본(Sonnet은 끌 수 없음)", "effort": "기본(medium)", "프롬프트": "지원 준비 정리(본문 근거만·추측 금지)"}
NOT_BY_SERVER = ["면접 후기(캐치·잡플래닛·채팅방은 로그인이 필요해 서버가 못 읽어요)", "합격 자소서 예시·합격자 스펙", "모의면접 질문 만들기", "내 경험과 연결(내 정보가 필요해 채팅에서)"]

PROMPT = (
    "아래는 채용공고 본문과 회사 정보입니다. 지원 준비 자료를 JSON 하나로만 답하세요. 설명 문장은 쓰지 마세요.\n"
    "규칙:\n"
    "1) 본문·회사 정보에 적힌 내용만 쓰세요. 없는 항목은 빈 문자열 \"\" 또는 빈 배열 []로 두세요. 추측·일반론으로 채우지 마세요.\n"
    "2) \"q\"(자소서 문항)는 본문에 문항과 글자 수가 실제로 적혀 있을 때만 넣으세요. 없으면 [].\n"
    "3) \"apply.url\"은 본문에 적힌 실제 지원 주소가 있을 때만 쓰세요(사람인·잡코리아 공고 주소는 쓰지 마세요).\n"
    "4) \"apply.steps\"는 본문에 적힌 지원 방법·전형 절차·제출 서류·마감 시각을 순서대로 짧은 문장으로.\n"
    "5) \"newsPick\"은 아래 뉴스 목록에서 이 직무 지원자가 알아 둘 만한 것의 번호(i)와 한 줄 요약(s, 제목에 있는 내용만)을 최대 5개.\n"
    "6) \"tips\"는 본문의 필수·우대·업무에서 서류·면접 때 강조할 점을 2~4개(본문 근거가 있는 것만).\n"
    "형식:\n"
    '{{"summary": "한 줄(지원 방법·마감 중심)", "jd": {{"dept": "", "summary": "", "role": [], "req": [], "plus": []}}, '
    '"apply": {{"url": "", "steps": []}}, "q": [{{"q": "", "limit": 0}}], "tips": [], "newsPick": [{{"i": 0, "s": ""}}]}}\n\n'
    "=== 공고 ===\n회사: {company}\n직무: {title}\n마감: {due}\n\n=== 본문 ===\n{text}\n\n=== 회사 정보 ===\n{info}\n\n=== 뉴스 제목(번호: 날짜 제목 · 언론사) ===\n{news}"
)


def requests_todo(db, limit: int) -> list[Document]:
    """처리할 요청: 상태가 비었거나 '요청됨' · 이미 정리된 키(prep)는 제외 · 실패는 2번까지 · 오래된 요청부터."""
    done = (db.get(SharedData, "prep").value if db.get(SharedData, "prep") else {}) or {}
    out = []
    for d in db.execute(select(Document).where(Document.collection == "prep").order_by(Document.updated_at)).scalars():
        v = d.value or {}
        if d.doc_id in done or v.get("status") not in (None, "", "요청됨", "서버 처리 실패"):
            continue
        if int(v.get("serverTries") or 0) >= MAX_TRIES:
            continue
        out.append(d)
    return out[:limit]


def find_job(db, req: dict, key: str) -> Job | None:
    """요청한 공고 찾기: 주소(번호) → 보드 키(sr번호) → 같은 회사의 가장 최근 공고."""
    jobs = list(db.execute(select(Job).order_by(Job.id.desc())).scalars())
    url = str(req.get("url") or "")
    if url:
        c = ss.canon(url)
        for j in jobs:
            if ss.canon(j.source_key) == c:
                return j
    m = re.match(r"^sr(\d+)$", key)
    if m:
        for j in jobs:
            if f"rec_idx={m.group(1)}" in j.source_key:
                return j
    name = short_name(req.get("company") or "")
    return next((j for j in jobs if name and short_name(j.company) == name), None)


def company_info(db, j: Job) -> str:
    comp = ((db.get(SharedData, "companies").value if db.get(SharedData, "companies") else {}) or {}).get((j.raw or {}).get("csn") or "", {})
    lines = [*(comp.get("sum") or []), *(comp.get("det") or []), *[str(x)[:600] for x in (comp.get("intro") or [])]]
    raw = j.raw or {}
    for k in ("규모·사원수", "회사 설명·제품"):
        if raw.get(k) and not any(str(raw[k]) in ln for ln in lines):
            lines.append(f"{k} {raw[k]}")
    return "\n".join(lines)[:2500] or "(회사 정보 없음)"


def news_for(db, j: Job) -> list[dict]:
    allnews = (db.get(SharedData, "companyNews").value if db.get(SharedData, "companyNews") else {}) or {}
    return (allnews.get(short_name(j.company), {}) or {}).get("items", [])[:12]


def ask_ai(prompt: str, key: str, model: str = co.AI_MODEL) -> dict:
    from scripts.test_image_read import estimate_cost
    res = ai_mod._call("anthropic", key, model, [{"role": "user", "text": prompt}], [], SETTINGS["max_tokens"])
    return {"data": ai_mod._parse_json(res["text"]) or {}, "model": model, "tokens_in": res["in"] or 0, "tokens_out": res["out"] or 0,
            "cost_usd": estimate_cost("anthropic", model, res["in"], res["out"]) or 0.0}


def _list(v) -> list[str]:
    if isinstance(v, str):
        v = [x.strip("- ").strip() for x in v.splitlines()]
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def build_entry(data: dict, news: list[dict], job: Job, by: str, today: date) -> dict:
    """AI 결과 → 서류 준비 자료(docs) 한 항목. 값이 비어 있는 칸은 아예 넣지 않음."""
    e: dict = {}
    jd = data.get("jd") or {}
    jd_out = {k: v for k, v in {"dept": str(jd.get("dept") or "").strip(), "summary": str(jd.get("summary") or "").strip(),
                                 "role": _list(jd.get("role")), "req": _list(jd.get("req")), "plus": _list(jd.get("plus"))}.items() if v}
    if jd_out:
        e["jd"] = {**jd_out, "src": f"공고 원문 · {by} {today.isoformat()}"}
    ap = data.get("apply") or {}
    steps = _list(ap.get("steps"))
    url = str(ap.get("url") or "").strip()
    if url and not re.match(r"^https?://", url):
        url = ""
    if url or steps:
        e["apply"] = {**({"url": url} if url else {}), **({"steps": steps} if steps else {})}
    qs = [{"q": str(q.get("q") or "").strip(), "limit": int(q.get("limit") or 0)} for q in (data.get("q") or []) if isinstance(q, dict) and str(q.get("q") or "").strip()]
    if qs:
        e["q"], e["qsrc"] = qs, f"공고 원문에 적힌 문항 · {by} {today.isoformat()} (실제 지원 화면에서 한 번 더 확인하세요)"
    tips = _list(data.get("tips"))
    if tips:
        e["tips"] = tips
    picks = []
    for p in data.get("newsPick") or []:
        try:
            n = news[int(p["i"])]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        picks.append({"d": n["d"], "t": n["t"], "s": str(p.get("s") or "").strip(), "src": n["src"], "u": n["u"]})
    if picks:
        e["news"] = picks
    if e:
        e["job"], e["updated"] = job.title, today.isoformat()
        e["auto"] = {"by": by, "date": today.isoformat(), "note": "서버가 공고 본문·회사 정보로 정리한 자료예요. 직접 쓴 칸은 덮어쓰지 않아요."}
    return e


def merge_entry(old: dict, new: dict) -> tuple[dict, list[str]]:
    """비어 있는 칸만 채움. 이미 있는 값(직접 쓴 것)은 그대로. 반환: (합친 항목, 새로 채운 칸 이름들)."""
    out, added = dict(old), []
    for k, v in new.items():
        if k in ("auto",):
            continue
        if old.get(k) in (None, "", [], {}):
            out[k] = v
            added.append(k)
        elif isinstance(old.get(k), dict) and isinstance(v, dict):
            sub = {kk: vv for kk, vv in v.items() if old[k].get(kk) in (None, "", [], {})}
            if sub:
                out[k] = {**old[k], **sub}
                added += [f"{k}.{kk}" for kk in sub]
    if added:
        out["auto"] = new.get("auto")
        out["updated"] = new.get("updated", old.get("updated"))
    return out, added


def _shared(db, key: str, default):
    row = db.get(SharedData, key)
    if row is None:
        row = SharedData(key=key, value=default)
        db.add(row)
    return row


def run(apply: bool = False, max_n: int = MAX_PER_DAY, email: str | None = None, fetch=None, ai=None, ref: date | None = None, say=print) -> dict:
    ref = ref or today_kst()
    init_db()
    fetch = fetch or co.Fetcher().get
    use = {"model": None, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0}
    out = {"mode": "저장함" if apply else "미리보기(저장 안 함)", "대상": 0, "정리함": 0, "실패": 0, "건너뜀": [], "AI 사용": use}
    with SessionLocal() as db:
        todo = requests_todo(db, max_n)
        out["대상"] = len(todo)
        if not apply:
            out["대상 예시"] = [f"{(d.value or {}).get('company', '?')} — {(d.value or {}).get('title', '')} ({d.doc_id})" for d in todo]
            say(f"→ 처리할 지원 준비 요청 {len(todo)}건(하루 최대 {max_n}). 저장하려면 --apply")
            return out
        rec = CollectRun(day=ref.isoformat(), mode="지원 준비", status="실행 중", options={"max": max_n})
        db.add(rec)
        db.commit()
        key, status, n_req = None, "완료", 0
        try:
            for d in todo:
                req, pkey = dict(d.value or {}), d.doc_id
                req["serverTries"] = int(req.get("serverTries") or 0) + 1

                def fail(why: str):
                    req.update({"status": "서버 처리 실패", "serverNote": why[:200], "serverAt": ref.isoformat()})
                    d.value = req
                    out["실패"] += 1
                    out["건너뜀"].append(f"{req.get('company', '?')}: {why}"[:200])
                    db.commit()
                j = find_job(db, req, pkey)
                if j is None:
                    fail("요청한 공고를 보드 DB에서 찾지 못함")
                    continue
                try:
                    text, why = source_text(j, fetch)
                    n_req += 0 if why == "저장된 본문" else 1
                    if len(text) < TEXT_MIN:                          # 이미지 공고 등: 수집기가 저장해 둔 필수·우대라도 근거로 씀
                        raw = j.raw or {}
                        text = "\n".join(f"{k}: {raw[k]}" for k in ("필수", "우대", "급여(공고)", "전형 절차", "근무 위치") if raw.get(k) and not gaps.is_blank(raw.get(k)))
                    if len(text) < TEXT_MIN:
                        fail(f"공고 본문을 읽지 못함({why})")
                        continue
                    news = news_for(db, j)
                    prompt = PROMPT.format(company=j.company, title=j.title, due=j.deadline_text or "-", text=text[:TEXT_MAX], info=company_info(db, j),
                                           news="\n".join(f"{i}: {n['d']} {n['t']} · {n['src']}" for i, n in enumerate(news)) or "(뉴스 없음)")
                    key = key or (ai and "test") or co._ai_key(db, email)
                    r = (ai or ask_ai)(prompt, key)
                except co.Blocked as e:
                    status, rec.blocked = "차단으로 중단", str(e)[:200]
                    out["중단"] = str(e)
                    break
                except Exception as e:                                   # 한 건이 실패해도 나머지는 계속
                    fail(f"{type(e).__name__}: {e}")
                    continue
                use["model"] = r.get("model") or use["model"]
                use["tokens_in"] += r.get("tokens_in", 0)
                use["tokens_out"] += r.get("tokens_out", 0)
                use["cost_usd"] += r.get("cost_usd") or 0.0
                use["calls"] += 1
                by = f"서버 AI {use['model'] or co.AI_MODEL}"
                entry = build_entry(r["data"], news, j, by, ref)
                if not entry:
                    fail("AI가 본문에서 정리할 내용을 찾지 못함")
                    continue
                docs_row = _shared(db, "admin:docs", {})
                docs = dict(docs_row.value or {})
                name = next((n for n in docs if short_name(n) == short_name(j.company)), j.company)
                merged, added = merge_entry(docs.get(name) or {}, entry)
                docs[name] = merged
                docs_row.value = docs
                summ = (r["data"].get("summary") or "").strip() or f"{j.company} 공고 본문·회사 정보 정리"
                prep_row = _shared(db, "prep", {})
                prep_row.value = {**(prep_row.value or {}), pkey: {"updated": ref.isoformat(), "summary": f"🤖 서버 자동 정리 — {summ}", "auto": True,
                                                                 "todo": NOT_BY_SERVER}}
                req.update({"status": "서버 처리 완료", "serverAt": ref.isoformat(), "채운 칸": added, "serverNote": "", "todo": NOT_BY_SERVER})
                d.value = req
                out["정리함"] += 1
                db.commit()
        finally:
            rec.finished_at, rec.status, rec.requests, rec.detail_requests = utcnow(), status, n_req, n_req
            rec.updated, rec.skipped, rec.ai_reads = out["정리함"], out["실패"], use["calls"]
            rec.ai_model, rec.ai_settings = (use["model"] or None), (SETTINGS if use["calls"] else None)
            rec.input_tokens, rec.output_tokens, rec.cost_usd = use["tokens_in"], use["tokens_out"], round(use["cost_usd"], 4)
            rec.notes = out["건너뜀"][:20] or None
            db.commit()
    say(f"→ 요청 {out['대상']}건 · 정리 {out['정리함']} · 실패 {out['실패']} · AI {use['calls']}번 · 토큰 {use['tokens_in']}/{use['tokens_out']} · ${use['cost_usd']:.4f}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="지원 준비 요청 서버 처리(기본: 미리보기)")
    ap.add_argument("--apply", action="store_true", help="실제로 저장(없으면 미리보기)")
    ap.add_argument("--max", type=int, default=MAX_PER_DAY, help=f"하루 최대 요청 수(기본 {MAX_PER_DAY})")
    ap.add_argument("--email", help="AI 키를 가진 계정 이메일(없으면 첫 관리자)")
    ap.add_argument("--sleep", type=float, nargs=2, default=(3.0, 5.0), metavar=("MIN", "MAX"))
    a = ap.parse_args(argv)
    f = co.Fetcher(sleep=tuple(a.sleep), max_requests=30)
    res = run(apply=a.apply, max_n=a.max, email=a.email, fetch=f.get)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 2 if res.get("중단") else 0


if __name__ == "__main__":
    sys.exit(main())
