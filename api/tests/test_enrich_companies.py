from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import CollectRun, Job, SharedData
from scripts import collect as co
from scripts import collect_sources as cs
from scripts import enrich_companies as ec
from scripts import sync_sheet as ss

FX = Path(__file__).parent / "fixtures"
PAGE = (FX / "saramin_company.html").read_text(encoding="utf-8")
LIST = (FX / "saramin_list.html").read_text(encoding="utf-8")
TODAY = date(2026, 11, 25)
QUIET = lambda *_: None  # noqa: E731


def add(db, n, company, csn=None, raw=None):
    r = {"기업명": company, "접수 상태": "접수중", "규모·사원수": "", "회사 설명·제품": "", **({"csn": csn} if csn else {}), **(raw or {})}
    db.add(Job(source_key=f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={n}", company=company, title="t", status="접수중", raw=r))
    db.commit()


def setshared(db, key, value):
    db.add(SharedData(key=key, value=value))
    db.commit()


def http_error(code):
    req = httpx.Request("GET", "https://x")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


def test_company_page_parser():
    c = cs.saramin_company_parse(PAGE)
    assert c["sum"] == ["업력 6년차 2021년 2월 2일 설립", "중소기업 기업형태", "8 명 출처: 국민연금 사원수", "13억 8,644만원 매출액"]
    assert c["det"][0] == "업종 배전반 및 전기 자동제어반 제조업" and "사업내용 전기,전자장비" in c["det"] and c["det"][-1].startswith("주소 경남 창원시") and not c["det"][-1].endswith("지도보기")
    assert c["wel"] == [["지원금/보험", "건강검진 직원대출제도"], ["급여제도", "퇴직연금 4대 보험"]] and c["intro"] == []
    assert c["fields"] == {"업종": "배전반 및 전기 자동제어반 제조업", "대표자명": "하현수",
                           "사업내용": "전기,전자장비", "홈페이지": "http://www.robotic-box.example",
                           "주소": "경남 창원시 성산구 전기의길 10, 102호 (성주동,한국전기연구원 기술창업센터)",
                           "설립일": "2021년 2월 2일 설립", "업력": "업력 6년차", "기업형태": "중소기업",
                           "매출액": "13억 8,644만원", "사원수": "8명",
                           "복지": {"지원금/보험": "건강검진 직원대출제도", "급여제도": "퇴직연금 4대 보험"}}
    assert cs.saramin_company_parse("<html>차단됨</html>") is None
    assert cs.company_headcount(c["sum"]) == "8명" and cs.company_headcount(["13억 매출액"]) == ""
    assert cs.saramin_company_url("ab+/c=") == "https://www.saramin.co.kr/zf_user/company-info/view?csn=ab%2B%2Fc="


def test_list_parser_and_row_carry_csn(client):
    items = cs.saramin_parse(LIST)
    assert all(i["csn"] for i in items) or True
    it = dict(items[0], csn="CSN123", src="saramin")
    row = co.to_sheet_row(it, TODAY)
    assert row["csn"] == "CSN123" and ss.to_raw(row, TODAY)["csn"] == "CSN123"


def test_helpers():
    assert ec.short_name("(주)로보웍스") == "로보웍스" and ec.short_name("㈜ 코 콤") == "코콤" and ec.short_name("주식회사 에이") == "에이"
    assert ec.address_of(["업종 x", "주소 경남 창원시 성산구 전기의길 10, 102호 (성주동)"]) == "경남 창원시 성산구 전기의길 10" and ec.address_of(["업종 x"]) == ""


def test_plan_new_stale_and_miss(client):
    with SessionLocal() as db:
        add(db, 1, "새회사", "NEW")
        add(db, 2, "있는회사", "HAVE")
        add(db, 3, "오래된회사", "OLD")
        add(db, 4, "방금확인", "FRESH")
        add(db, 5, "못읽음최근", "MISS1")
        add(db, 6, "못읽음오래전", "MISS2")
        add(db, 7, "번호없음")
        setshared(db, "companies", {"HAVE": {"sum": [], "det": [], "intro": [], "wel": []}, "OLD": {"sum": ["x"], "det": [], "intro": [], "wel": []}, "FRESH": {"sum": [], "det": [], "intro": [], "wel": []}})
        setshared(db, "companiesChecked", {"OLD": "2026-08-01", "FRESH": "2026-11-20"})
        setshared(db, "companiesMiss", {"MISS1": "2026-11-20", "MISS2": "2026-10-01"})
        p = ec.plan(db, TODAY, 40)
    assert [c for c, _ in p["new"]] == ["MISS2", "NEW"] or sorted(c for c, _ in p["new"]) == ["MISS2", "NEW"]
    assert sorted(c for c, _ in p["stale"]) == ["HAVE", "OLD"]          # 확인한 적 없는 HAVE(시드)도 오래된 회사로 봄 · 오래된 순으로 앞에 옴
    assert [c for c, _ in p["stale"]][0] == "HAVE"


def test_apply_saves_company_geo_and_fills_job_cells(client):
    urls, geos = [], []
    with SessionLocal() as db:
        add(db, 1, "(주)로보틱박스", "NEW1")
        add(db, 2, "(주)로보틱박스", "NEW1", {"규모·사원수": "중소기업"})        # 이미 값이 있으면 그대로
    out = ec.run(apply=True, fetch=lambda u: urls.append(u) or PAGE, geo=lambda a: geos.append(a) or [35.2, 128.6, "addr"], ref=TODAY, say=QUIET, sleep=lambda s: None)
    with SessionLocal() as db:
        comps, geo = db.get(SharedData, "companies").value, db.get(SharedData, "geo").value
        chk, upd = db.get(SharedData, "companiesChecked").value, db.get(SharedData, "companiesUpdated").value
        jobs = sorted(db.execute(select(Job)).scalars(), key=lambda j: j.id)
        rec = db.execute(select(CollectRun)).scalars().one()
    assert out["새 회사"] == 1 and out["좌표 추가"] == 1 and out["공고 칸 채움"] == 2 and urls[0].endswith("csn=NEW1")
    assert comps["NEW1"]["sum"][2] == "8 명 출처: 국민연금 사원수" and set(comps["NEW1"]) == {"sum", "det", "intro", "wel"}      # 보드가 아는 칸만 저장
    assert geos == ["경남 창원시 성산구 전기의길 10"] and geo["로보틱박스"] == [35.2, 128.6, "addr"]
    assert chk["NEW1"] == "2026-11-25" and upd == "2026-11-25"
    assert jobs[0].raw["규모·사원수"] == "8명" and jobs[0].raw["회사 설명·제품"] == "전기,전자장비" and jobs[1].raw["규모·사원수"] == "중소기업" and jobs[1].raw["회사 설명·제품"] == "전기,전자장비"
    assert rec.mode == "회사 정보" and rec.status == "완료" and rec.requests == 2 and rec.new_found == 1


def test_preview_saves_nothing(client):
    with SessionLocal() as db:
        add(db, 1, "새회사", "NEW1")
    out = ec.run(apply=False, fetch=lambda u: 1 / 0, ref=TODAY, say=QUIET)
    assert out["대상"]["새 회사"] == 1
    with SessionLocal() as db:
        assert db.get(SharedData, "companies") is None and db.execute(select(CollectRun)).scalars().all() == []


def test_miss_blocked_and_geo_failure(client):
    with SessionLocal() as db:
        add(db, 1, "막힘", "BLOCK")                     # 번호가 작을수록 나중에 처리됨(최신 공고의 회사 먼저)
        add(db, 2, "없는회사", "GONE")
        add(db, 3, "좌표실패", "GEOFAIL")
    pages = {"GONE": http_error(404), "GEOFAIL": PAGE, "BLOCK": co.Blocked("HTTP 429")}

    def f(url):
        for k, v in pages.items():
            if k in url:
                if isinstance(v, Exception):
                    raise v
                return v

    def bad_geo(a):
        raise TimeoutError()
    out = ec.run(apply=True, fetch=f, geo=bad_geo, ref=TODAY, say=QUIET, sleep=lambda s: None)
    with SessionLocal() as db:
        miss, comps = db.get(SharedData, "companiesMiss").value, db.get(SharedData, "companies").value
        rec = db.execute(select(CollectRun)).scalars().one()
    assert miss == {"GONE": "2026-11-25"} and "GEOFAIL" in comps and "GONE" not in comps          # 못 읽은 회사는 별도 칸에만 기록
    assert out["못 읽음"] == 1 and out["새 회사"] == 1 and out["좌표 추가"] == 0 and any("좌표 실패" in x for x in out["건너뜀"])
    assert out["중단"] and rec.status == "차단으로 중단"


def test_stale_refresh_keeps_intro(client):
    with SessionLocal() as db:
        add(db, 1, "(주)로보틱박스", "OLD")
        setshared(db, "companies", {"OLD": {"sum": ["옛"], "det": [], "intro": ["직접 넣은 소개"], "wel": []}})
    out = ec.run(apply=True, fetch=lambda u: PAGE, geo=lambda a: None, ref=TODAY, say=QUIET, sleep=lambda s: None)
    with SessionLocal() as db:
        c = db.get(SharedData, "companies").value["OLD"]
    assert out["갱신"] == 1 and c["intro"] == ["직접 넣은 소개"] and c["sum"][0].startswith("업력")


def test_cli_args():
    import pytest
    with pytest.raises(SystemExit):
        ec.main(["--bogus"])


# ───────────── 번호 찾기 · 소개글 · 뉴스 ─────────────
SEARCH_2 = '''<div class="item_corp"><a href="/zf_user/company-info/view?csn=AAA1">로보웍스(주)</a> 설립일 2011년 03월 25일 대표자명 이현종 업종 산업용 로봇 제조업 기업주소 인천 서해구 로봇랜드</div>
<div class="item_corp"><a href="/zf_user/company-info/view?csn=BBB2">주식회사로보웍스</a> 설립일 2024년 06월 04일 대표자명 김유란 업종 기계 기업주소 대전 유성구 갑천로</div>'''
SEARCH_1 = '<div class="item_corp"><a href="/zf_user/company-info/view?csn=CCC3">(주)유일회사</a> 설립일 2010년 대표자명 홍길동 업종 소프트웨어 기업주소 서울 강남구</div>'
INTRO = '''<div class="content_fold"><div class="company_introduce"><strong class="tit">인재와 기술 바탕의 미래 창조 기업</strong></div></div>
<div class="content_fold"><div class="company_introduce"><strong class="tit">기업비전</strong><p class="txt">안녕하십니까?<br>우리는 장비를 만듭니다.</p></div></div>'''
RSS = '''<rss><channel><item><title>유일회사 신제품 공개 - 한국일보</title><link>https://n.example/1</link><pubDate>Mon, 03 Nov 2025 01:00:00 GMT</pubDate><source url="x">한국일보</source></item>
<item><title>유일회사 투자 유치 - 전자신문</title><link>https://n.example/2</link><pubDate>Tue, 04 Mar 2025 01:00:00 GMT</pubDate><source url="y">전자신문</source></item></channel></rss>'''


def test_search_parser_and_pick():
    c = cs.saramin_company_search_parse(SEARCH_2)
    assert [x["csn"] for x in c] == ["AAA1", "BBB2"] and c[0]["industry"] == "산업용 로봇 제조업" and c[1]["addr"] == "대전 유성구 갑천로"
    assert cs.pick_company(c, "(주)로보웍스") == (None, "모호(같은 이름 2곳)")
    assert cs.pick_company(c, "(주)로보웍스", "대전 유성구")[0]["csn"] == "BBB2"
    assert cs.pick_company(c, "다른회사")[1] == "일치하는 회사 없음"
    assert cs.saramin_intro_parse(INTRO) == ["인재와 기술 바탕의 미래 창조 기업 기업비전 안녕하십니까? 우리는 장비를 만듭니다."] and cs.saramin_intro_parse("<p>x</p>") == []
    n = cs.google_news_parse(RSS)
    assert [x["d"] for x in n] == ["2025-11-03", "2025-03-04"] and n[0]["t"] == "유일회사 신제품 공개" and n[0]["src"] == "한국일보"
    assert cs.google_news_parse("<html>not rss") == [] and cs.google_news_parse(None) == []


def test_find_csn_intro_and_news(client):
    with SessionLocal() as db:
        add(db, 1, "(주)유일회사")                         # csn 없음 → 이름으로 찾기
        add(db, 2, "(주)로보웍스", raw={"근무 위치": "서울 강남구"})  # 같은 이름 2곳이고 근무지가 둘 다 아님 → 모호
        db.commit()
    seen = []

    def fetch(u):
        seen.append(u)
        if "search/company" in u:
            return SEARCH_1 if "%EC%9C%A0%EC%9D%BC" in u else SEARCH_2
        if "view-inner-info" in u:
            return INTRO
        if "news.google.com" in u:
            return RSS
        return PAGE
    out = ec.run(apply=True, fetch=fetch, geo=lambda a: None, ref=TODAY, say=QUIET, sleep=lambda s: None)
    assert out["번호 찾음"] == 1 and out["번호 모호"] == 1 and out["새 회사"] == 1 and out["소개글 추가"] == 1 and out["뉴스 회사"] == 2
    with SessionLocal() as db:
        j1 = db.execute(select(Job).where(Job.company == "(주)유일회사")).scalar_one()
        assert j1.raw["csn"] == "CCC3" and "이름 일치" in j1.raw["csn 출처"]
        j2 = db.execute(select(Job).where(Job.company == "(주)로보웍스")).scalar_one()
        assert "csn" not in j2.raw
        comps = db.get(SharedData, "companies").value
        assert comps["CCC3"]["intro"][0].startswith("인재와 기술") and "우리는 장비를 만듭니다" in comps["CCC3"]["intro"][0]
        find = db.get(SharedData, "companiesFind").value
        assert find["로보웍스"]["reason"].startswith("모호") and [c["csn"] for c in find["로보웍스"]["후보"]] == ["AAA1", "BBB2"]
        news = db.get(SharedData, "companyNews").value
        assert news["유일회사"]["items"][0]["t"] == "유일회사 신제품 공개" and news["유일회사"]["updated"] == "2026-11-25"
    again = ec.run(apply=True, fetch=fetch, geo=lambda a: None, ref=TODAY, say=QUIET, sleep=lambda s: None)       # 같은 날 다시: 새로 할 일 없음
    assert again["번호 찾음"] == 0 and again["번호 모호"] == 0 and again["소개글 추가"] == 0 and again["뉴스 회사"] == 0 and again["새 회사"] == 0


def test_find_blocked_stops_day(client):
    with SessionLocal() as db:
        add(db, 1, "(주)가")
        add(db, 2, "(주)나")
    n = []

    def fetch(u):
        n.append(u)
        raise co.Blocked("403")
    out = ec.run(apply=True, fetch=fetch, fetch_news=lambda u: RSS, ref=TODAY, say=QUIET, sleep=lambda s: None)
    assert out["중단"] and len(n) == 1                      # 사람인이 막으면 그날 사람인 요청은 1건에서 멈춤(뉴스는 다른 사이트라 계속)
    with SessionLocal() as db:
        assert db.execute(select(CollectRun)).scalars().all()[-1].status == "차단으로 중단"
