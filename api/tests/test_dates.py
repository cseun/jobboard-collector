from datetime import date

from sqlalchemy import select, text

from app import dates
from app.db import SessionLocal, engine, init_db
from app.models import Job
from scripts import backfill_dates
from scripts import sync_sheet as ss
from tests.test_sheet_sync import HEAD_C, write_csv

TODAY = date(2026, 11, 24)


def test_parse_date_formats():
    assert dates.parse_date("2026-11-24") == date(2026, 11, 24)
    assert dates.parse_date("26/11/24") == date(2026, 11, 24)       # 사람인 등록일 표기
    assert dates.parse_date("11/24", TODAY) == date(2026, 11, 24)
    assert dates.parse_date("12/30", TODAY) == date(2025, 12, 30)   # 기준일보다 미래면 작년
    assert dates.parse_date("2026-13-40") is None and dates.parse_date("") is None and dates.parse_date(None) is None


def test_backfill_first_seen_marks_estimate_and_is_idempotent():
    raw = {"최종 확인": "11/20"}
    assert dates.backfill_first_seen(raw, TODAY) is True
    assert raw["최초 수집"] == "2026-11-20" and raw["최초 수집 추정"] is True
    assert dates.backfill_first_seen(raw, TODAY) is False
    assert dates.backfill_first_seen({"최종 확인": ""}, TODAY) is False


def test_seed_import_fills_first_seen(seeded):
    with SessionLocal() as db:
        jobs = db.execute(select(Job)).scalars().all()
    assert jobs and all((j.raw.get("최초 수집") and j.first_seen) for j in jobs if j.raw.get("최종 확인"))


def test_reimport_keeps_collector_dates(seeded):
    from scripts.import_seed import run as import_seed
    with SessionLocal() as db:
        j = db.execute(select(Job)).scalars().first()
        raw = dict(j.raw); raw["등록일"] = "2026-11-01"; raw["최초 수집"] = "2026-11-02"
        j.raw = raw; key = j.source_key
        db.commit()
    import_seed()
    with SessionLocal() as db:
        j = db.execute(select(Job).where(Job.source_key == key)).scalar_one()
    assert j.raw["등록일"] == "2026-11-01" and j.raw["최초 수집"] == "2026-11-02"
    assert j.posted_date == date(2026, 11, 1) and j.first_seen == date(2026, 11, 2)


def test_sheet_new_rows_get_first_seen_and_posted(seeded, tmp_path):
    head = HEAD_C + ["등록일"]
    url = "https://www.saramin.co.kr/zf_user/jobs/relay/view-detail?rec_idx=77777701"
    rows = [[1, "사람인", url, "날짜로보", "ROS2 개발자", "신입", "대졸", "2026-12-05", "2026-11-24", "26/11/20"]]
    ss.run(write_csv(tmp_path / "c.csv", head, rows), apply=True, today=TODAY)
    with SessionLocal() as db:
        j = db.execute(select(Job).where(Job.company == "날짜로보")).scalar_one()
    assert j.raw["최초 수집"] == "2026-11-24" and j.raw["등록일"] == "2026-11-20"
    assert j.first_seen == TODAY and j.posted_date == date(2026, 11, 20)


def test_init_db_adds_missing_columns_to_old_table(client):
    with engine.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS ix_jobs_posted_date"))
        c.execute(text("DROP INDEX IF EXISTS ix_jobs_first_seen"))
        c.execute(text("ALTER TABLE jobs DROP COLUMN posted_date"))
        c.execute(text("ALTER TABLE jobs DROP COLUMN first_seen"))
    init_db(); init_db()   # 두 번 해도 안전
    with engine.connect() as c:
        cols = {r[1] for r in c.execute(text("PRAGMA table_info(jobs)"))}
    assert {"posted_date", "first_seen"} <= cols


def test_backfill_script_preview_then_apply(seeded):
    with SessionLocal() as db:
        for j in db.execute(select(Job)).scalars():
            raw = dict(j.raw); raw.pop("최초 수집", None); raw.pop("최초 수집 추정", None)
            j.raw = raw; j.first_seen = None
        db.commit()
    pre = backfill_dates.run(False, TODAY)
    assert pre["최초 수집을 짐작해 채움"] > 0
    with SessionLocal() as db:
        assert all(j.first_seen is None for j in db.execute(select(Job)).scalars())   # 미리보기는 저장 안 함
    backfill_dates.run(True, TODAY)
    again = backfill_dates.run(True, TODAY)
    assert again["최초 수집을 짐작해 채움"] == 0
    with SessionLocal() as db:
        assert any(j.first_seen for j in db.execute(select(Job)).scalars())


def test_jobs_api_sort_and_fields(seeded, admin_client):
    with SessionLocal() as db:
        js = db.execute(select(Job).order_by(Job.id)).scalars().all()
        js[0].first_seen = date(2030, 1, 1); js[1].first_seen = date(2030, 1, 20)   # 시드 값보다 확실히 최근
        js[0].posted_date = date(2030, 1, 25); js[1].posted_date = date(2030, 1, 3)
        a, b = js[0].id, js[1].id
        db.commit()
    r = admin_client.get("/api/jobs?sort=first_seen&limit=2").json()
    items = r["items"] if isinstance(r, dict) else r
    assert items[0]["id"] == b and "first_seen" in items[0] and "posted_date" in items[0]
    r = admin_client.get("/api/jobs?sort=posted&limit=2").json()
    items = r["items"] if isinstance(r, dict) else r
    assert items[0]["id"] == a
    assert admin_client.get("/api/jobs?sort=bogus").status_code == 422


def test_cleanup_gubun(seeded):
    from scripts import cleanup_gubun
    with SessionLocal() as db:
        js = db.execute(select(Job).order_by(Job.id)).scalars().all()
        for j, g in zip(js[:3], ["[신규·시트]", "[신규·시트]", "[신규·수집]"]):
            raw = dict(j.raw); raw["구분"] = g; j.raw = raw
        db.commit()
    assert cleanup_gubun.run(False)["'[신규·시트]' → '[이월]'"] == 2
    with SessionLocal() as db:
        assert sum(1 for j in db.execute(select(Job)).scalars() if j.raw.get("구분") == "[신규·시트]") == 2   # 미리보기는 저장 안 함
    cleanup_gubun.run(True)
    assert cleanup_gubun.run(True)["'[신규·시트]' → '[이월]'"] == 0
    with SessionLocal() as db:
        gs = [j.raw.get("구분") for j in db.execute(select(Job)).scalars()]
    assert "[신규·시트]" not in gs and "[신규·수집]" in gs and gs.count("[이월]") >= 2
