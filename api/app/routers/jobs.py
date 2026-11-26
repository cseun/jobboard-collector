from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DBSession

from ..db import get_db
from ..deps import get_current_user
from ..models import Job, SharedData, User

router = APIRouter(prefix="/api", tags=["jobs"], dependencies=[Depends(get_current_user)])


def _summary(j: Job) -> dict:
    return {
        "id": j.id,
        "company": j.company,
        "title": j.title,
        "location": j.location,
        "status": j.status,
        "deadline_text": j.deadline_text,
        "deadline_date": j.deadline_date,
        "deadline_time": j.deadline_time,
        "last_checked": j.last_checked,
        "posted_date": j.posted_date,
        "first_seen": j.first_seen,
        "url": j.source_key,
    }


@router.get("/jobs")
def list_jobs(
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, max_length=50),
    sort: str = Query(default="id", pattern="^(id|first_seen|posted|deadline)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: DBSession = Depends(get_db),
):
    stmt = select(Job)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Job.company.ilike(like), Job.title.ilike(like)))
    if status:
        stmt = stmt.where(Job.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    order = {"id": [Job.id], "first_seen": [Job.first_seen.desc().nulls_last(), Job.id],       # 보드에 최근 들어온 순
             "posted": [Job.posted_date.desc().nulls_last(), Job.id],                       # 사이트에 최근 등록된 순
             "deadline": [Job.deadline_date.asc().nulls_last(), Job.id]}[sort]              # 마감 임박 순
    rows = db.execute(stmt.order_by(*order).limit(limit).offset(offset)).scalars().all()
    return {"total": total, "items": [_summary(r) for r in rows]}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: DBSession = Depends(get_db)):
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "공고를 찾을 수 없습니다.")
    return {**_summary(j), "raw": j.raw}


@router.get("/board-data")
def board_data(user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    """기존 보드의 `const DATA = {...}`와 같은 모양으로 돌려줍니다(2단계에서 보드가 이걸 불러옴)."""
    jobs = [r.raw for r in db.execute(select(Job).order_by(Job.id)).scalars().all()]
    out = {"jobs": jobs}
    for row in db.execute(select(SharedData)).scalars().all():
        if row.key.startswith("admin:"):
            # 본인 맞춤 자료: 관리자에게는 원래 키로, 그 밖에는 빈 값으로(보드가 없는 경우를 처리함)
            out[row.key[len("admin:"):]] = row.value if user.is_admin else ({} if isinstance(row.value, dict) else [])
        else:
            out[row.key] = row.value
    return out


@router.get("/shared/{key}")
def get_shared(key: str, db: DBSession = Depends(get_db)):
    row = db.get(SharedData, key)
    if row is None:
        raise HTTPException(404, "데이터를 찾을 수 없습니다.")
    return row.value
