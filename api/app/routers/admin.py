from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from ..db import get_db
from ..deps import require_admin
from .. import gaps, reviews
from ..dates import today_kst
from ..models import CollectRun, DemoUser, InviteCode, Job, SharedData, User, utcnow
from ..schemas import FillIn, InviteCreateIn, JobImportIn, ReviewImportIn
from ..security import new_invite_code

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _view(i: InviteCode) -> dict:
    return {
        "id": i.id,
        "code": i.code,
        "note": i.note,
        "created_at": i.created_at,
        "expires_at": i.expires_at,
        "used": i.used_by is not None,
        "used_at": i.used_at,
    }


@router.post("/invites", status_code=201)
def create_invite(body: InviteCreateIn, admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    expires = utcnow() + timedelta(days=body.expires_days) if body.expires_days else None
    for _ in range(5):  # 코드 충돌은 사실상 없지만 안전하게 재시도
        code = new_invite_code()
        if not db.execute(select(InviteCode).where(InviteCode.code == code)).scalar_one_or_none():
            break
    inv = InviteCode(code=code, created_by=admin.id, note=body.note, expires_at=expires)
    db.add(inv)
    db.commit()
    return _view(inv)


@router.get("/invites")
def list_invites(admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    rows = db.execute(select(InviteCode).order_by(InviteCode.id.desc())).scalars().all()
    return [_view(r) for r in rows]


@router.delete("/invites/{invite_id}", status_code=204)
def delete_invite(invite_id: int, admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    inv = db.get(InviteCode, invite_id)
    if inv is None:
        raise HTTPException(404, "초대 코드를 찾을 수 없습니다.")
    if inv.used_by is not None:
        raise HTTPException(409, "이미 사용된 코드는 지울 수 없습니다.")
    db.delete(inv)
    db.commit()


@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    rows = db.execute(select(User).where(User.id.not_in(select(DemoUser.user_id))).order_by(User.id)).scalars().all()
    return [{"id": u.id, "email": u.email, "is_admin": u.is_admin, "created_at": u.created_at} for u in rows]


def run_view(r: CollectRun, full: bool = False) -> dict:
    out = {c: getattr(r, c) for c in ("id", "day", "started_at", "finished_at", "mode", "status", "keywords", "requests", "list_pages", "listed", "known",
                                      "new_found", "skipped", "saved_new", "updated", "detail_requests", "detail_ok", "image_postings", "ai_reads", "ai_model",
                                      "ai_settings", "input_tokens", "output_tokens", "cost_usd", "blocked", "options")}
    if full:
        out["notes"], out["per_keyword"] = r.notes, r.per_keyword
    return out


@router.get("/collect-runs")
def collect_runs(limit: int = 30, admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    """수집기 실행 기록(최근 순). 숫자와 설정만 있고 공고 내용은 없습니다."""
    rows = db.execute(select(CollectRun).order_by(CollectRun.id.desc()).limit(max(1, min(limit, 200)))).scalars().all()
    return {"items": [run_view(r) for r in rows]}


@router.get("/collect-runs/{run_id}")
def collect_run(run_id: int, admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    r = db.get(CollectRun, run_id)
    if r is None:
        raise HTTPException(404, "기록을 찾을 수 없습니다.")
    return run_view(r, full=True)


@router.get("/gaps")
def list_gaps(fields: str | None = Query(default=None, max_length=200), limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
              include_closed: bool = False, admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    """빈칸이 있는 공고 목록(마감 제외가 기본). fields=필수,우대 처럼 칸을 고르면 그 칸이 빈 공고만 나옵니다.
    summary는 칸별 빈 공고 수. 내 PC가 이 목록을 받아 찾아본 뒤 /jobs/{id}/fill 로 돌려보냅니다."""
    want = tuple(f.strip() for f in fields.split(",") if f.strip()) if fields else gaps.GAP_FIELDS
    bad = [f for f in want if f not in gaps.GAP_FIELDS]
    if bad:
        raise HTTPException(422, f"빈칸을 조회할 수 없는 칸: {', '.join(bad)} (가능: {', '.join(gaps.GAP_FIELDS)})")
    jobs = [j for j in db.execute(select(Job).order_by(Job.id.desc())).scalars() if include_closed or gaps.is_active(j)]
    summary = {f: 0 for f in want}
    items = []
    for j in jobs:
        it = gaps.gap_item(j, want)
        if it:
            items.append(it)
            for f in it["blanks"]:
                summary[f] += 1
    return {"total": len(items), "summary": summary, "items": items[offset:offset + limit]}


@router.post("/jobs/{job_id}/fill")
def fill_job(job_id: int, body: FillIn, admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    """공고의 빈칸을 채웁니다. 기본은 빈칸만 채우고(이미 값이 있으면 건너뜀), overwrite=true 일 때만 덮어씁니다."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "공고를 찾을 수 없습니다.")
    res = gaps.apply_fill(job, body.fields, by=body.by, overwrite=body.overwrite)
    db.commit()
    return {"id": job.id, **res, "blanks": gaps.blanks_of(job.raw)}


# ---- 내 PC의 Chrome이 로그인 상태로 모은 면접 후기·합격 자소서 예시를 받는 통로(관리자 전용) ----
@router.get("/review-targets")
def review_targets(limit: int = Query(default=5, ge=1, le=20), admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    """후기를 모을 회사 목록(지원 준비 요청 → 내 지원 현황 순, 최근 60일 안에 모은 회사 제외). 이미 읽은 주소(known_urls)는 건너뛰면 됩니다."""
    return {"day": today_kst().isoformat(), "targets": reviews.targets(db, admin.id, today_kst(), limit)}


@router.post("/review-import")
def review_import(body: ReviewImportIn, admin: User = Depends(require_admin), db: DBSession = Depends(get_db)):
    """한 회사의 후기·합격 자소서 예시를 합침(덧붙이기만, 기존 글은 건드리지 않음). 같은 주소는 한 번만 반영."""
    ref = today_kst()
    new = [r.model_dump() for r in body.rows]
    iv_row = db.get(SharedData, "interviews") or SharedData(key="interviews", value=[])
    db.add(iv_row)
    merged, n_rows = reviews.merge_interviews(list(iv_row.value or []), body.company, new, ref)
    if n_rows:
        iv_row.value = merged
    n_ex = 0
    if body.examples:
        d_row = db.get(SharedData, "admin:docs") or SharedData(key="admin:docs", value={})
        db.add(d_row)
        docs, n_ex = reviews.merge_examples(dict(d_row.value or {}), body.company, [e.model_dump() for e in body.examples], ref)
        if n_ex:
            d_row.value = docs
    reviews.record_import(db, body.company, ref)          # 아무것도 못 찾은 회사도 날짜를 남겨 60일 동안 다시 읽지 않음
    db.commit()
    return {"company": body.company, "후기 반영": n_rows, "예시 반영": n_ex, "건너뜀(이미 있는 주소)": (len(new) - n_rows) + (len(body.examples) - n_ex)}


# ---- 서버가 못 읽는 사이트(원티드 403)를 내 PC Chrome이 읽어 보내는 통로(관리자 전용) ----
WANTED_PAGES = 3          # 키워드당 읽을 쪽 수(쪽당 20건)


@router.get("/job-plan")
def job_plan(source: str = Query(default="wanted"), pages: int = Query(default=WANTED_PAGES, ge=1, le=5), admin: User = Depends(require_admin),
             db: DBSession = Depends(get_db)):
    """내 PC Chrome이 읽을 목록 주소(키워드 × 쪽)와 이미 아는 공고 번호(known). 읽은 JSON에서 아는 공고를 뺀 핵심 칸만 /job-import 의 items 로 보내면 됩니다."""
    from scripts import collect as co
    from scripts.collect_sources import SOURCES
    if source not in SOURCES or source != "wanted":
        raise HTTPException(400, "지원하는 사이트는 wanted 입니다.")
    src = SOURCES[source]
    kws = co.load_keywords()
    known = sorted({k.split(":", 1)[1] for k in (co.known_keys(db) | set(co.drop_cache(db))) if k.startswith(source + ":")})
    return {"source": source, "pages_per_keyword": pages, "keywords": kws, "known": known,
            "urls": [{"keyword": k, "page": p, "url": src.url(k, p)} for k in kws for p in range(1, pages + 1)]}


@router.post("/job-import")
def job_import(body: JobImportIn, admin: User = Depends(require_admin)):
    """내 PC Chrome이 읽은 목록 JSON을 서버 규칙(아는 공고·경력/학력/제외어·AI 제목 판정·저장)으로 처리. 목록 정보만 저장(상세는 읽지 않음)."""
    from scripts import collect as co
    return co.import_listing(body.source, [p.model_dump() for p in body.pages], apply=body.apply, email=admin.email, compact=[i.model_dump() for i in body.items])
