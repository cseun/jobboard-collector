from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job, UserKV
from scripts import cleanup_excluded as ce


def _add(db, key, company, title, gubun="[신규·수집]"):
    db.add(Job(source_key=key, company=company, title=title, raw={"구분": gubun}))


def test_preview_apply_and_safeguards(client):
    from scripts.create_admin import create
    uid = None
    with SessionLocal() as db:
        _add(db, "https://www.saramin.co.kr/x?rec_idx=111111", "A사", "생산직 오퍼레이터 모집")
        _add(db, "https://www.saramin.co.kr/x?rec_idx=222222", "B사", "로봇 SW 개발자")
        _add(db, "https://www.saramin.co.kr/x?rec_idx=333333", "C사", "튜터 모집")            # 내 데이터에 쓰임
        _add(db, "https://www.saramin.co.kr/x?rec_idx=444444", "D사", "교사 채용", "[이월]")  # 수집기 공고 아님
        db.commit()
    create("u@example.com", "correct-horse-battery")
    from app.models import User
    with SessionLocal() as db:
        uid = db.execute(select(User.id)).scalars().first()
        db.add(UserKV(user_id=uid, key="apps", value={"x": "rec_idx=333333 지원함"}))
        db.commit()
    words = ["생산직", "튜터", "교사"]
    r = ce.run(False, words)
    assert r["mode"].startswith("미리보기") and r["지울 공고"] == 1
    assert len(r["내 데이터에 쓰여서 남김"]) == 1 and r["수집기 공고가 아니라 건너뜀(--all로 포함)"] == 1
    with SessionLocal() as db:
        assert db.execute(select(Job)).scalars().all().__len__() == 4          # 미리보기는 지우지 않음
    ce.run(True, words)
    with SessionLocal() as db:
        left = {j.company for j in db.execute(select(Job)).scalars()}
    assert left == {"B사", "C사", "D사"}
    r2 = ce.run(True, words, include_all=True)                                   # --all이면 이월 공고도 대상
    with SessionLocal() as db:
        assert {j.company for j in db.execute(select(Job)).scalars()} == {"B사", "C사"}
    assert r2["지울 공고"] == 1


def test_full_list_includes_urls_and_all_flag(client):
    from app.db import SessionLocal
    from app.models import Job
    from scripts import cleanup_excluded as ce
    with SessionLocal() as db:
        db.add(Job(source_key="https://x/sheet1", company="S", title="UX 디자이너", raw={"구분": "신규", "관련도": "×"}))
        db.add(Job(source_key="https://x/col1", company="C", title="기획", raw={"구분": "[신규·수집]", "관련도": "×"}))
        db.commit()
    assert ce.run(False, [], use_x=True)["지울 공고"] == 1                            # 기본은 수집기 공고만
    r = ce.run(False, [], include_all=True, use_x=True, full=True)
    assert r["지울 공고"] == 2 and any("https://x/sheet1" in x for x in r["목록(전체, 주소 포함)"])
