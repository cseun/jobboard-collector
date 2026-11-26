from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job
from app.routers.jobs import _visible
from scripts.multi_role import import_roles, relevant
from scripts.requirements_audit import audit


def test_target_filter_is_conservative():
    assert relevant("로봇 SW 개발")
    assert relevant("AI·LLM 엔지니어")
    assert relevant("AI Enablement")
    assert not relevant("AI Scientist [약물설계]")
    assert not relevant("AI 마케팅")
    assert not relevant("웹 백엔드 개발")
    assert not relevant("품질관리")


def test_split_creates_independent_jobs_and_keeps_parent(client):
    with SessionLocal() as db:
        parent = Job(source_key="https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=99999999",
                     company="테스트회사", title="신입/경력 수시채용", location="충북", status="마감",
                     deadline_text="11/25", deadline_date=date(2026, 11, 25),
                     first_seen=date(2026, 11, 24),
                     raw={"기업명": "테스트회사", "공고명·담당 업무": "신입/경력 수시채용",
                          "원문 URL": "[사람인](https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=99999999)",
                          "원문 본문": "원본 OCR 본문", "필수": "", "우대": ""})
        db.add(parent)
        db.commit()
        payload = {"parent_source_key": parent.source_key,
                   "roles": [{"title": "로봇 SW 개발", "required": ["ROS 경험"], "preferred": ["C++ 경험"]},
                             {"title": "AI 마케팅", "required": ["광고 운영"]},
                             {"title": "PLC 자동화 제어", "required": ["PLC 개발"]}]}
        preview = import_roles(db, payload)
        assert len(preview["selected"]) == 2 and preview["excluded"] == ["AI 마케팅"]
        assert len(db.execute(select(Job)).scalars().all()) == 1
        import_roles(db, payload, apply=True)
        rows = db.execute(select(Job).order_by(Job.id)).scalars().all()
        assert len(rows) == 3 and not _visible(rows[0])
        assert all(_visible(j) for j in rows[1:])
        assert rows[1].source_key != rows[2].source_key
        assert rows[1].raw["필수"] == "- ROS 경험"
        assert rows[2].raw["필수"] == "- PLC 개발"
        assert all(j.raw["원문 본문"] == "원본 OCR 본문" for j in rows[1:])
        assert all(j.first_seen == date(2026, 11, 24) for j in rows[1:])
        assert audit(rows)["total"] == 2
        import_roles(db, payload, apply=True)
        assert len(db.execute(select(Job)).scalars().all()) == 3
