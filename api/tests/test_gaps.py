from datetime import date

from sqlalchemy import select

from app import gaps
from app.db import SessionLocal
from app.models import Job


def _jobs(db):
    return db.execute(select(Job).order_by(Job.id)).scalars().all()


def test_is_blank_rules():
    for v in (None, "", "  ", "원문 확인 필요", "미확인", [], {}):
        assert gaps.is_blank(v)
    for v in ("—", "C++ 3년", "없음", ["a"]):
        assert not gaps.is_blank(v)          # '—'는 '없음'이라는 뜻이라 빈칸이 아님


def test_gaps_list_summary_and_filters(seeded, admin_client):
    with SessionLocal() as db:
        j = _jobs(db)
        raw = dict(j[0].raw); raw["필수"] = "원문 확인 필요"; raw["원문 본문"] = "본문 내용"; raw["이미지 주소"] = ["https://x/1.jpg"]
        j[0].raw = raw; j[0].status = "접수중"
        raw = dict(j[1].raw); raw["필수"] = ""; j[1].raw = raw; j[1].status = "마감"
        db.commit(); first = j[0].id; closed = j[1].id
    r = admin_client.get("/api/admin/gaps?fields=필수&limit=500").json()
    ids = {i["id"] for i in r["items"]}
    assert first in ids and closed not in ids                       # 마감은 기본 제외
    assert r["summary"]["필수"] == r["total"] and all(i["blanks"] == ["필수"] for i in r["items"])
    it = next(i for i in r["items"] if i["id"] == first)
    assert it["원문 본문"] == "본문 내용" and it["이미지 주소"] == ["https://x/1.jpg"]
    assert closed in {i["id"] for i in admin_client.get("/api/admin/gaps?fields=필수&limit=500&include_closed=true").json()["items"]}
    assert admin_client.get("/api/admin/gaps?fields=기업명").status_code == 422
    assert len(admin_client.get("/api/admin/gaps?limit=1").json()["items"]) == 1


def test_fill_only_blank_and_records_source(seeded, admin_client):
    with SessionLocal() as db:
        j = _jobs(db)[0]
        raw = dict(j.raw); raw["필수"] = "원문 확인 필요"; raw["우대"] = "ROS2"; raw.pop("등록일", None); j.raw = raw
        db.commit(); jid = j.id
    r = admin_client.post(f"/api/admin/jobs/{jid}/fill", json={"fields": {"필수": ["C++ 3년", "리눅스"], "우대": "Python", "등록일": "26/11/20", "기업명": "해킹"}, "by": "내PC"}).json()
    assert set(r["applied"]) == {"필수", "등록일"}
    assert r["skipped"]["우대"] == "이미 값이 있음" and r["skipped"]["기업명"] == "채울 수 없는 칸"
    with SessionLocal() as db:
        j = db.get(Job, jid)
    assert j.raw["필수"] == "- C++ 3년\n- 리눅스" and j.raw["우대"] == "ROS2" and j.raw["등록일"] == "2026-11-20" and j.posted_date == date(2026, 11, 20)
    assert j.raw["채움 기록"]["필수"].startswith("내PC ") and j.company != "해킹"
    # 덮어쓰기는 명시했을 때만
    r = admin_client.post(f"/api/admin/jobs/{jid}/fill", json={"fields": {"우대": "Python"}, "overwrite": True}).json()
    assert r["applied"] == ["우대"]


def test_fill_rejects_bad_values(seeded, admin_client):
    with SessionLocal() as db:
        j = _jobs(db)[0]
        raw = dict(j.raw); raw.pop("등록일", None); raw["급여(공고)"] = ""; j.raw = raw; db.commit(); jid = j.id
    r = admin_client.post(f"/api/admin/jobs/{jid}/fill", json={"fields": {"등록일": "2099-01-01", "급여(공고)": "원문 확인 필요", "필수": "가" * 5000}}).json()
    assert r["applied"] == [] and "날짜" in r["skipped"]["등록일"] and r["skipped"]["급여(공고)"] == "빈 값" and "너무 김" in r["skipped"]["필수"]
    assert admin_client.post("/api/admin/jobs/999999/fill", json={"fields": {"필수": "a"}}).status_code == 404
    assert admin_client.post(f"/api/admin/jobs/{jid}/fill", json={"fields": {"필수": "a"}, "by": "<script>"}).status_code == 422


def test_gaps_admin_only(client):
    assert client.get("/api/admin/gaps").status_code in (401, 403)
    assert client.post("/api/admin/jobs/1/fill", json={"fields": {"필수": "a"}}).status_code in (401, 403)
