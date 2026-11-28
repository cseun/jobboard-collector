"""이미 저장한 공고의 '마감'을 갱신합니다. 기본은 미리보기, --apply 를 붙여야 저장합니다. 수집(새 공고) 뒤에 이어서 돕니다.

    docker compose exec -T api python -m scripts.refresh_status                 # 미리보기
    docker compose exec -T api python -m scripts.refresh_status --apply --max 150

두 단계:
  1) (요청 0건) 마감일이 오늘보다 앞선 접수 중 공고를 '마감'으로 바꿈
  2) '미정 마감' 공고를 먼저, 나머지는 오래 확인 안 한 순서로 하루 --max 건(기본 150) 다시 열어 봄
     - 사이트에서 삭제된 공고(404·410) → 마감 · 원티드는 status 가 active 가 아니면 마감
     - 사람인·잡코리아는 공고 페이지의 '마감일'을 읽어 바뀌었으면(연장 등) 고치고, 이미 지났으면 마감
     - '채용시 마감'·상시 공고는 날짜가 없으므로 삭제되기 전까지는 접수 중으로 둠
- 최종 확인(`최종 확인`)은 바꾸지 않고 `마감 확인일`에 오늘을 남김(옛 신규 표시 규칙에 영향을 주지 않으려고).
- 이미 '마감'인 공고는 다시 열지 않음. 한 사이트가 막히면(403·429) 그 사이트만 멈추고 나머지는 계속.
"""
import argparse
import json
import sys
from datetime import date

import httpx
from sqlalchemy import select

from app import gaps
from app.dates import today_kst
from app.db import SessionLocal, init_db
from app.models import CollectRun, Job, utcnow
from scripts import collect as co
from scripts import sync_sheet as ss
from scripts.collect_sources import STATUS_CHECK

MAX_PER_DAY = 150
CHECKED = "마감 확인일"
ONGOING = ("상시", "채용시", "수시")


def _md(d: date) -> str:
    return f"{d.month}/{d.day}"


def close(job: Job, why: str, ref: date) -> None:
    raw = dict(job.raw or {})
    raw["접수 상태"], raw[CHECKED], raw["마감 사유"] = "마감", ref.isoformat(), why
    job.raw, job.status = raw, "마감"


def set_due(job: Job, due: date, ref: date) -> None:
    raw = dict(job.raw or {})
    raw["마감(KST)"], raw[CHECKED] = _md(due), ref.isoformat()
    job.raw, job.deadline_text, job.deadline_date, job.deadline_time = raw, _md(due), due, None   # 연도는 사이트가 준 값을 그대로 씀


def free_close(db, ref: date, apply: bool) -> list[Job]:
    hit = [j for j in db.execute(select(Job)).scalars() if gaps.is_active(j) and j.deadline_date and j.deadline_date < ref]
    if apply:
        for j in hit:
            close(j, f"마감일 {_md(j.deadline_date)} 지남", ref)
    return hit


def unknown_deadline(job: Job) -> bool:
    """날짜와 '상시·채용시·수시' 확인도 없는 화면의 '미정 마감' 공고."""
    if job.deadline_date is not None:
        return False
    raw = job.raw or {}
    label = str(raw.get("마감(KST)") or job.deadline_text or "").replace(" ", "")
    return not any(word in label for word in ONGOING)


def candidates(db, ref: date, sources: list[str], limit: int) -> list[Job]:
    out = []
    for j in db.execute(select(Job).order_by(Job.id)).scalars():
        raw = j.raw or {}
        kind = ss.canon(j.source_key).split(":")[0]
        if not gaps.is_active(j) or kind not in sources or kind not in STATUS_CHECK or raw.get(CHECKED) == ref.isoformat():
            continue
        out.append(j)
    out.sort(key=lambda j: (0 if unknown_deadline(j) else 1, (j.raw or {}).get(CHECKED) or "", j.id))
    return out[:limit]


def check_one(job: Job, fetch, ref: date) -> tuple[str, str]:
    """('closed'|'due'|'open'|'unknown', 설명) 을 돌려주고 필요한 변경을 job에 바로 반영합니다."""
    kind, rid = ss.canon(job.source_key).split(":", 1)
    url_of, parse = STATUS_CHECK[kind]
    try:
        text = fetch(url_of(rid))
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (404, 410):
            close(job, "사이트에서 삭제됨", ref)
            return "closed", "사이트에서 삭제됨"
        raise
    st = parse(text)
    if st is None:
        raw = dict(job.raw or {})
        raw[CHECKED] = ref.isoformat()                 # 계속 못 읽는 공고가 매일 맨 앞을 차지하지 않게 돌려 보냄
        job.raw = raw
        return "unknown", "마감 정보를 읽지 못함(페이지 모양 변경?)"
    if st.get("closed"):
        close(job, "사이트가 마감 처리", ref)
        return "closed", "사이트가 마감 처리"
    due = st.get("due")
    if due and due < ref:
        close(job, f"마감일 {_md(due)} 지남", ref)
        return "closed", f"마감일 {_md(due)} 지남"
    if due and due != job.deadline_date:
        set_due(job, due, ref)
        return "due", f"마감일 {_md(due)}로 갱신"
    raw = dict(job.raw or {})
    raw[CHECKED] = ref.isoformat()
    if not due and st.get("kind") in ONGOING and unknown_deadline(job):
        raw["마감(KST)"] = st["kind"]
        job.deadline_text = st["kind"]
    job.raw = raw
    return "open", ""


def run(apply: bool = False, max_n: int = MAX_PER_DAY, sources: list[str] | None = None, fetch=None, ref: date | None = None, say=print) -> dict:
    ref = ref or today_kst()
    sources = sources or list(STATUS_CHECK)
    init_db()
    fetch = fetch or co.Fetcher(max_requests=max_n + 20).get
    out = {"mode": "저장함" if apply else "미리보기(저장 안 함)", "날짜로 마감": 0, "다시 확인": 0, "삭제·마감으로 바뀜": 0, "마감일 갱신": 0, "그대로": 0, "읽지 못함": 0, "건너뜀": []}
    blocked: set[str] = set()
    with SessionLocal() as db:
        free = free_close(db, ref, apply)
        out["날짜로 마감"] = len(free)
        todo = candidates(db, ref, sources, max_n)
        unknown_total = sum(1 for j in db.execute(select(Job)).scalars()
                            if gaps.is_active(j) and unknown_deadline(j))
        unknown_supported = sum(1 for j in db.execute(select(Job)).scalars()
                                if gaps.is_active(j) and unknown_deadline(j)
                                and ss.canon(j.source_key).split(":")[0] in sources)
        out["미정 마감 전체"] = unknown_total
        out["미정 마감 재점검 가능"] = unknown_supported
        out["미정 마감 출처 미지원"] = unknown_total - unknown_supported
        out["이번 회차 미정 마감"] = sum(unknown_deadline(j) for j in todo)
        if not apply:
            out["다시 확인할 공고"] = len(todo)
            say(f"→ 마감일 지나 마감 처리할 공고 {len(free)}건 · 다시 확인할 공고 {len(todo)}건(하루 최대 {max_n}). 저장하려면 --apply")
            return out
        rec = CollectRun(day=ref.isoformat(), mode="마감 갱신", status="실행 중", options={"max": max_n, "sources": sources})
        db.add(rec)
        db.commit()
        status, n_req = "완료", 0
        try:
            for j in todo:
                kind = ss.canon(j.source_key).split(":")[0]
                if kind in blocked:
                    continue
                try:
                    n_req += 1
                    res, why = check_one(j, fetch, ref)
                except co.Blocked as e:
                    blocked.add(kind)
                    status, rec.blocked = "차단으로 중단", f"[{kind}] {e}"[:200]
                    out["중단"] = rec.blocked
                    continue
                except Exception as e:                    # 한 건이 실패해도 나머지는 계속(서버 오류 등)
                    out["건너뜀"].append(f"{j.company}: {type(e).__name__}"[:120])
                    raw = dict(j.raw or {})
                    raw[CHECKED] = ref.isoformat()         # 계속 실패하는 공고가 매일 맨 앞을 차지하지 않게 돌려 보냄
                    j.raw = raw
                    continue
                out["다시 확인"] += 1
                if res == "closed":
                    out["삭제·마감으로 바뀜"] += 1
                elif res == "due":
                    out["마감일 갱신"] += 1
                elif res == "unknown":
                    out["읽지 못함"] += 1
                    out["건너뜀"].append(f"{j.company}: {why}")
                else:
                    out["그대로"] += 1
                db.commit()
        finally:
            rec.finished_at, rec.status, rec.requests, rec.detail_requests = utcnow(), status, n_req, n_req
            rec.known, rec.updated = out["다시 확인"], out["날짜로 마감"] + out["삭제·마감으로 바뀜"] + out["마감일 갱신"]
            rec.skipped = out["읽지 못함"]
            rec.notes = [f"날짜로 마감 {out['날짜로 마감']}건", f"삭제·마감 {out['삭제·마감으로 바뀜']}건", f"마감일 갱신 {out['마감일 갱신']}건"] + out["건너뜀"][:17]
            db.commit()
    say(f"→ 날짜로 마감 {out['날짜로 마감']}건 · 다시 확인 {out['다시 확인']}건(삭제·마감 {out['삭제·마감으로 바뀜']} · 마감일 갱신 {out['마감일 갱신']} · 그대로 {out['그대로']} · 읽지 못함 {out['읽지 못함']})")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="저장된 공고의 마감 갱신(기본: 미리보기)")
    ap.add_argument("--apply", action="store_true", help="실제로 저장(없으면 미리보기)")
    ap.add_argument("--max", type=int, default=MAX_PER_DAY, help=f"하루에 다시 열어 볼 공고 수(기본 {MAX_PER_DAY})")
    ap.add_argument("--source", default="all", help="saramin · jobkorea · wanted · all(쉼표 가능)")
    ap.add_argument("--sleep", type=float, nargs=2, default=(3.0, 5.0), metavar=("MIN", "MAX"))
    a = ap.parse_args(argv)
    srcs = list(STATUS_CHECK) if a.source.strip() == "all" else [x.strip() for x in a.source.split(",") if x.strip()]
    bad = [x for x in srcs if x not in STATUS_CHECK]
    if bad:
        ap.error(f"모르는 사이트: {', '.join(bad)}")
    f = co.Fetcher(sleep=tuple(a.sleep), max_requests=a.max + 20)
    res = run(apply=a.apply, max_n=a.max, sources=srcs, fetch=f.get)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 2 if res.get("중단") else 0


if __name__ == "__main__":
    sys.exit(main())
