from pathlib import Path

from scripts.collect import parse_detail
from scripts.text_requirements import parse_requirements, parse_sections

FX = Path(__file__).parent / "fixtures"


def test_real_sample_text():
    t = parse_detail((FX / "saramin_detail_text.html").read_text(encoding="utf-8"))["text"]
    r = parse_requirements(t)
    assert "경력 3년 이상" in r["필수"] and "대학교졸업" in r["필수"]
    assert "임베디드 리눅스 어플리케이션" not in r["필수"]       # 업무 구역은 섞이지 않음


def test_basic_sections_and_bullets():
    t = "주요업무\n- ROS 개발\n자격요건\n1. 컴퓨터공학 전공\n2) C++ 가능자\n우대사항\n• ROS2 경험자\n• 영어 가능자\n근무조건\n정규직"
    r = parse_requirements(t)
    assert r["필수"] == "- 컴퓨터공학 전공\n- C++ 가능자" and r["우대"] == "- ROS2 경험자\n- 영어 가능자"


def test_sections_keep_work_benefits_and_process_separate():
    text = "주요업무\n- 로봇 제어\n필수 요건\n- C++ 개발\n우대 사항\n- ROS2\n복리후생\n- 식대 지원\n채용절차\n서류 → 면접"
    sections = parse_sections(text)
    assert sections == {"담당 업무": "- 로봇 제어", "필수": "- C++ 개발", "우대": "- ROS2",
                        "복지": "- 식대 지원", "전형 절차": "- 서류 → 면접"}


def test_inline_and_bracket_headings():
    t = "[필수 요건] : 경력 무관\n[우대 사항]\n- 자격증 보유자\n※ 채용 절차\n서류 → 면접"
    assert parse_requirements(t) == {"필수": "- 경력 무관", "우대": "- 자격증 보유자"}


def test_mixed_heading_is_not_split():
    t = "📋 주요업무\n[주요업무 / 자격요건]\n- 개발 업무\n- C/C++ 경험자\n📋 우대사항\n- 펌웨어 경험"
    r = parse_requirements(t)
    assert "필수" not in r and r["우대"] == "- 펌웨어 경험"


def test_nothing_found_and_long_sentence_not_heading():
    assert parse_requirements("") == {}
    assert parse_requirements("저희 회사는 자격요건을 엄격히 봅니다. 많은 지원 바랍니다.") == {}
