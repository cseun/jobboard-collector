from datetime import date

from app.models import Job
from scripts import backfill_details as bd


TODAY = date(2026, 11, 27)


def _job(raw=None):
    return Job(source_key="https://www.jobkorea.co.kr/Recruit/GI_Read/123", company="에이로봇", title="로봇 SW",
               status="접수중", raw=raw or {"기업명": "에이로봇", "원문 URL": "https://www.jobkorea.co.kr/Recruit/GI_Read/123"})


def test_verified_backfill_fills_blanks_without_overwriting_user_value():
    j = _job({"필수": "- 사용자가 확인한 조건", "원문 본문": ""})
    info = {"text": "담당 업무\n로봇 SW 개발\n필수 요건\nROS2 경험\n우대 사항\nGit 경험", "images": [], "is_image": False}
    assert bd.fill(j, info, TODAY) == "업무·필수 확인"
    assert j.raw["필수"] == "- 사용자가 확인한 조건" and j.raw["담당 업무"] == "- 로봇 SW 개발"
    assert j.raw["우대"] == "- Git 경험" and j.raw["상세 확인일"] == TODAY.isoformat()


def test_image_or_missing_body_stays_unclassified():
    j = _job()
    assert bd.fill(j, {"text": "", "images": ["https://example.org/posting.png"], "is_image": True,
                       "company_name": "(주)에이로봇", "company_fields": {"업종": "산업용 로봇 제조업", "사원수": "54명"}}, TODAY) == "이미지 OCR 검증 대기"
    assert "필수" not in j.raw and j.raw["이미지 주소"] == ["https://example.org/posting.png"]
    assert j.raw["회사 정보"]["업종"] == "산업용 로봇 제조업"
    assert j.raw["상세 재조회 시도"] == 1
