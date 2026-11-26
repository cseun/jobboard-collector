"""이미 들어 있는 공고에 날짜 칸(최초 수집·DB 날짜 칸)을 채웁니다. 기본은 미리보기(저장 안 함).

    docker compose exec -T api python -m scripts.backfill_dates            # 미리보기
    docker compose exec -T api python -m scripts.backfill_dates --apply    # 저장

- '최초 수집'이 없는 공고: '최종 확인' 날짜로 짐작해 넣고 '최초 수집 추정'을 표시합니다(정확한 값이 아님).
- '등록일'은 여기서 채우지 않습니다(공고 페이지를 열어야 알 수 있어서 보강기가 채움).
- 이미 값이 있는 공고는 건드리지 않아 여러 번 실행해도 같습니다.
"""
import argparse
import json
import sys

from sqlalchemy import select

from app.dates import RAW_FIRST, backfill_first_seen, fill_job_dates, today_kst
from app.db import SessionLocal, init_db
from app.models import Job


def run(apply: bool = False, today=None) -> dict:
    ref = today or today_kst()
    init_db()
    est = nodate = cols = 0
    with SessionLocal() as db:
        for job in db.execute(select(Job)).scalars():
            raw = dict(job.raw or {})
            if backfill_first_seen(raw, ref):
                est += 1
                if apply:
                    job.raw = raw          # 새 dict를 넣어야 변경이 저장됩니다
            elif not raw.get(RAW_FIRST):
                nodate += 1                # '최종 확인'도 없어 짐작할 수 없음
            before = (job.posted_date, job.first_seen)
            if apply:
                fill_job_dates(job)
                cols += before != (job.posted_date, job.first_seen)
        if apply:
            db.commit()
    return {"mode": "저장함" if apply else "미리보기(저장 안 함)", "최초 수집을 짐작해 채움": est, "짐작할 수 없음(최종 확인 없음)": nodate,
            "DB 날짜 칸 갱신": cols if apply else "(저장할 때 계산)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="공고 날짜 칸 채우기(기본: 미리보기)")
    ap.add_argument("--apply", action="store_true", help="실제로 저장")
    a = ap.parse_args(argv)
    print(json.dumps(run(a.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
