from datetime import date
from html import escape

from scripts import collect as co
from scripts.collect_sources import jobkorea_parse_detail, jobkorea_status


def _page(body: str, due: str = "2026.12.09") -> str:
    srcdoc = escape(f"<html><body>{body}</body></html>", quote=True)
    return (f'<meta name="writer" content="에이로봇">'
            f'<meta name="description" content="마감일 : {due}">'
            f'<iframe srcdoc="{srcdoc}"></iframe>'
            '<div>기업 정보</div><div>사원수</div><div>54명</div>'
            '<div>기업구분</div><div>중소기업</div><div>산업(업종)</div><div>산업용 로봇 제조업</div>'
            '<div>위치</div><div>경기 안산시</div>'
            '<div>추천공고</div><div>우대 사항: 전혀 다른 회사의 경력</div>')


def test_jobkorea_srcdoc_only_and_company_fields():
    html = _page('<h3>담당 업무</h3><p>로봇 SW 개발</p><h3>필수 요건</h3><p>ROS2 개발 경험</p><h3>우대 사항</h3><p>Git 경험</p>')
    d = jobkorea_parse_detail(html)
    assert d["company_name"] == "에이로봇"
    assert d["company_fields"]["업종"] == "산업용 로봇 제조업"
    assert "추천공고" not in d["text"] and "ROS2 개발 경험" in d["text"]
    assert not d["is_image"]
    row = {"_detail": d}
    p = {"src": "jobkorea", "title": "로봇 SW 개발", "company": "에이로봇"}
    assert co.quality_reason(p, row, {"fields": d["company_fields"]}, jobkorea_status(html), date(2026, 11, 27)) == ""
    assert row["_sections"]["필수"] == "- ROS2 개발 경험"


def test_jobkorea_image_and_outer_page_text_cannot_pass():
    img = jobkorea_parse_detail(_page('<img src="https://example.org/posting.png">'))
    assert img["is_image"] and img["images"] == ["https://example.org/posting.png"]
    assert co.quality_reason({"src": "jobkorea", "title": "로봇 SW"}, {"_detail": img}, {"fields": {"업종": "로봇 제조업"}},
                             {"due": date(2026, 12, 9), "kind": "date"}, date(2026, 11, 27))
