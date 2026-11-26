from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CollectRun, Job
from scripts import collect as co
from scripts import collect_sources as cs

FX = Path(__file__).parent / "fixtures"
JK = (FX / "jobkorea_list.html").read_text(encoding="utf-8")
WL = (FX / "wanted_list.json").read_text(encoding="utf-8")
WD = (FX / "wanted_detail.json").read_text(encoding="utf-8")
SARAMIN_LIST = (FX / "saramin_list.html").read_text(encoding="utf-8")
TODAY = date(2026, 11, 25)
QUIET = lambda *_: None  # noqa: E731


def fake_fetch(pages):
    calls = []

    def f(url):
        calls.append(url)
        for k, v in pages.items():
            if k in url:
                return v
        return "{}" if "api" in url else "<html></html>"
    f.calls = calls
    return f


def test_jobkorea_parse_cards():
    items = {i["id"]: i for i in cs.jobkorea_parse(JK)}
    assert list(items) == ["50089017", "50300001", "50300002", "50300003"]
    a = items["50089017"]                                   # 로고 링크 + 제목 링크가 따로 있는 카드
    assert (a["title"], a["company"], a["loc"], a["career"]) == ("임베디드엔지니어링 직무 인재모집", "신세계I&C", "서울 강남구 외 1", "경력")
    b = items["50300001"]                                   # 링크가 카드 전체를 감싼 카드: 첫 글자 덩어리가 제목, 회사 이름이 '서울'로 시작해도 지역으로 착각하지 않음
    assert (b["title"], b["company"], b["loc"], b["career"]) == ("[서울반도체] 임베디드 펌웨어 개발자", "서울반도체㈜", "경기 안산시", "경력3년↑")
    assert (b["day_kind"], b["day"], b["due_raw"]) == ("등록일", "10/01", "12/31 마감")
    assert items["50300002"]["career"] == "신입" and items["50300002"]["day_kind"] == ""
    assert cs.jobkorea_next(JK, 1, 4) and not cs.jobkorea_next("<html></html>", 1, 4) and cs.jobkorea_next("<html></html>", 1, 20)


def test_wanted_parse_list_and_detail():
    items = cs.wanted_parse(WL)
    assert [i["id"] for i in items] == ["144230", "397093", "387010"]
    assert items[0]["career"] == "경력 3~5년" and items[1]["career"] == "신입·경력" and items[0]["company"] == "에스알브이포지셔닝"
    assert cs.wanted_next(WL, 1) and not cs.wanted_next('{"links":{"next":null},"data":[]}', 1)
    assert cs.wanted_parse("not json") == [] and cs.wanted_parse_detail("not json") == {}
    d = cs.wanted_parse_detail(WD)
    assert d["loc"] == "서울 강남구" and d["due"] == "2026-12-20" and d["active"] and "ROS2" in d["req"] and "Nav2" in d["pref"]
    assert cs.wanted_parse_detail('{"job":{"due_time":null,"detail":{}}}')["due"] == ""


def test_urls_and_keys():
    assert cs.SOURCES["wanted"].view("397093") == "https://www.wanted.co.kr/wd/397093"
    assert "Page_No=2" in cs.jobkorea_url("로봇", 2) and "offset=20" in cs.wanted_url("로봇", 2)
    from scripts import sync_sheet as ss
    for name, rid in (("jobkorea", "50300001"), ("wanted", "397093"), ("saramin", "55661373")):
        assert ss.canon(cs.SOURCES[name].view(rid)) == f"{name}:{rid}"


def test_crawl_other_site_and_known(client):
    f = fake_fetch({"Search/?stext": JK})
    r = co.crawl(["각"], {"jobkorea:50300002"}, f, TODAY, stop_known=99, max_pages=1, say=QUIET, src=cs.SOURCES["jobkorea"])
    assert [p["id"] for p in r["new"]] == ["50089017", "50300001", "50300003"] and all(p["src"] == "jobkorea" for p in r["new"])
    row = co.to_sheet_row(r["new"][1], TODAY)
    assert row["site"] == "잡코리아" and row["url"].endswith("/Recruit/GI_Read/50300001") and row["origin"] == "잡코리아 수집" and row["posted"] == "10/01"


def test_apply_all_sources_keeps_ids_apart_and_fills_wanted_details(client):
    pages = {"chaos/search/v1/position": WL, "api/v4/jobs/": WD, "Search/?stext": JK, "searchword=": SARAMIN_LIST}
    f = fake_fetch(pages)
    out = co.run(["각"], apply=True, details=True, fetch=f, ref=TODAY, say=QUIET, stop_known=99, max_pages=1, max_career=5, keep_grad=True,
                 sources=["saramin", "jobkorea", "wanted"])
    with SessionLocal() as db:
        jobs = list(db.execute(select(Job)).scalars())
        keys = {j.source_key for j in jobs}
        rec = db.execute(select(CollectRun)).scalars().one()
    assert out["저장한 새 공고"] == len(jobs)
    assert any("GI_Read/50300001" in k for k in keys) and any("/wd/397093" in k for k in keys) and any("rec_idx=55661373" in k for k in keys)
    # 경력 8년 요구(시니어랩)는 걸러지고, 3~5년(최소 3)과 신입·경력은 저장
    assert not any("/wd/387010" in k for k in keys) and any("/wd/144230" in k for k in keys)
    w = next(j for j in jobs if "/wd/397093" in j.source_key)
    assert w.location == "서울 강남구" and w.deadline_text == "12/20" and w.raw["출처"] == "원티드 수집"
    assert "ROS2" in w.raw["필수"] and "Nav2" in w.raw["우대"] and w.raw["필수·우대 출처"] == "원티드 API" and "로봇 내비게이션" in w.raw["원문 본문"]
    jk = next(j for j in jobs if "GI_Read/50300001" in j.source_key)
    assert jk.raw["출처"] == "잡코리아 수집" and jk.raw["등록일"].endswith("-10-01") and "원문 본문" not in jk.raw     # 잡코리아는 상세를 읽지 않음
    assert rec.options["sources"] == ["saramin", "jobkorea", "wanted"] and rec.detail_requests >= 2
    assert {k["사이트"] for k in rec.per_keyword} == {"사람인", "잡코리아", "원티드"}


def test_one_site_blocked_others_continue(client):
    def f(url):
        if "wanted" in url:
            raise co.Blocked("HTTP 429")
        return JK if "jobkorea" in url else "<html></html>"
    out = co.run(["각"], apply=True, fetch=f, ref=TODAY, say=QUIET, stop_known=99, max_pages=1, max_career=-1, sources=["wanted", "jobkorea"])
    assert out["중단"].startswith("[원티드]") and out["저장한 새 공고"] == 4        # 원티드는 막혔지만 잡코리아는 저장
    with SessionLocal() as db:
        assert db.execute(select(CollectRun)).scalars().one().status == "차단으로 중단"


def test_cli_rejects_unknown_source():
    import pytest
    with pytest.raises(SystemExit):
        co.main(["--source", "indeed"])
