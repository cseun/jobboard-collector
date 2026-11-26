"""보드의 공고 510건과 공용 데이터를 DB에 넣습니다. 여러 번 실행해도 중복 없이 갱신됩니다.

    python -m scripts.import_seed
"""
import json
import os
import re
import sys
from datetime import date

from sqlalchemy import select

from app import config
from app.db import SessionLocal, init_db
from app.dates import RAW_FIRST, RAW_FIRST_EST, RAW_POSTED, backfill_first_seen, fill_job_dates
from app.deadline import parse_deadline
from app.models import Job, SharedData

_URL = re.compile(r"\((https?://[^)\s]+)\)|(https?://[^\s|)\]]+)")


def first_url(cell: str) -> str | None:
    m = _URL.search(cell or "")
    return (m.group(1) or m.group(2)) if m else None


def load(name: str):
    with open(os.path.join(config.SEED_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def run() -> dict:
    init_db()
    jobs = load("jobs.json")
    shared = load("shared.json")
    ref = date.fromisoformat(shared.get("updated") or date.today().isoformat())
    # 본인 맞춤 자료(docs)는 저장소 밖 비공개 폴더에 있을 때만 가져옵니다(관리자 전용 키로 저장).
    docs_path = os.path.join(config.PRIVATE_DIR, "docs.json")
    if os.path.isfile(docs_path):
        with open(docs_path, encoding="utf-8") as f:
            shared["docs"] = json.load(f)

    added = updated = 0
    with SessionLocal() as db:
        existing = {j.source_key: j for j in db.execute(select(Job)).scalars()}
        for idx, raw in enumerate(jobs):
            key = first_url(raw.get("원문 URL", "")) or f"nourl:{raw.get('기업명')}:{raw.get('공고명·담당 업무')}:{idx}"
            d, t = parse_deadline(raw.get("마감(KST)"), ref)
            fields = dict(
                company=raw.get("기업명") or "",
                title=raw.get("공고명·담당 업무") or "",
                location=raw.get("근무 위치"),
                status=raw.get("접수 상태"),
                deadline_text=raw.get("마감(KST)"),
                deadline_date=d,
                deadline_time=t,
                last_checked=raw.get("최종 확인"),
                raw=raw,
            )
            row = existing.get(key)
            if row is not None:   # 시드에는 없는 날짜 칸(수집기·시트가 넣은 값)은 지우지 않고 유지
                for dk in (RAW_POSTED, RAW_FIRST, RAW_FIRST_EST):
                    if dk in (row.raw or {}) and dk not in raw:
                        raw[dk] = row.raw[dk]
            backfill_first_seen(raw, ref)   # '최초 수집'이 없으면 '최종 확인'으로 짐작해 채움(추정 표시)
            fields["raw"] = raw
            if row is None:
                row = Job(source_key=key, **fields)
                fill_job_dates(row)
                db.add(row)
                added += 1
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
                fill_job_dates(row)
                updated += 1

        # 예전 버전이 모두에게 열린 'docs' 키로 넣어 둔 값이 있으면 지웁니다.
        for k in config.ADMIN_ONLY_SHARED:
            stale = db.get(SharedData, k)
            if stale is not None:
                db.delete(stale)
        for k, v in shared.items():
            if k in config.ADMIN_ONLY_SHARED:  # 본인 맞춤 자료는 관리자에게만 내려가는 키로 저장
                k = "admin:" + k
            row = db.get(SharedData, k)
            if row is None:
                db.add(SharedData(key=k, value=v))
            else:
                row.value = v
        db.commit()
    return {"jobs_added": added, "jobs_updated": updated, "shared_keys": len(shared)}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
    sys.exit(0)
