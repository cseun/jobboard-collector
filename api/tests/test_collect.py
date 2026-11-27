from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job
from scripts import collect as co

FX = Path(__file__).parent / "fixtures"
LIST = (FX / "saramin_list.html").read_text(encoding="utf-8")
DET_TEXT = (FX / "saramin_detail_text.html").read_text(encoding="utf-8")
DET_IMG = (FX / "saramin_detail_image.html").read_text(encoding="utf-8")
TODAY = date(2026, 11, 25)


def test_parse_list_fields():
    items = co.parse_list(LIST)
    assert [i["id"] for i in items] == ["55661373", "55660876", "55660581", "55657889"]
    a = items[0]
    assert a["title"] == "임베디드 리눅스 개발자 - 정규직 채용" and a["company"] == "에이치알코어스"
    assert a["loc"] == "서울 강남구" and a["career"] == "경력3년↑" and a["edu"] == "대졸↑" and a["emp"] == "정규직"
    assert (a["day_kind"], a["day"]) == ("등록일", "26/11/24") and a["due_raw"] == "~ 12/09(수)"
    assert items[2]["day_kind"] == "수정일"
    assert co.has_next(LIST, 1) and not co.has_next(LIST, 2)
    assert co.has_next("<html></html>", 1, 40) and not co.has_next("<html></html>", 1, 39)   # 링크를 못 찾아도 꽉 찬 쪽이면 다음 쪽이 있다고 봄


def test_due_fields():
    assert co.due_fields("~ 12/09(수)", TODAY) == {"due": "12/9"}
    assert co.due_fields("채용시", TODAY) == {"due_state": "채용시"}
    assert co.due_fields("상시채용", TODAY) == {"due_state": "상시채용"}
    assert co.due_fields("내일마감", TODAY) == {"due": "11/26"} and co.due_fields("오늘마감", TODAY) == {"due": "11/25"}


def test_sheet_row_posted_only_for_register_label():
    items = {i["id"]: i for i in co.parse_list(LIST)}
    assert co.to_sheet_row(items["55661373"], TODAY)["posted"] == "26/11/24"
    assert "posted" not in co.to_sheet_row(items["55660581"], TODAY)       # 수정일은 등록일이 아님


def test_parse_detail_text_and_image():
    t = co.parse_detail(DET_TEXT)
    assert not t["is_image"] and "임베디드 리눅스" in t["text"] and t["start"] == "2026-11-24"
    i = co.parse_detail(DET_IMG)
    assert i["is_image"] and i["images"] == ["https://pds.saramin.co.kr/recruit/2026/11/24/abc_1.jpg", "https://pds.saramin.co.kr/recruit/2026/11/24/abc_2.png"]


def test_quality_requires_structured_detail_company_and_live_deadline():
    from scripts.collect_sources import saramin_company_parse
    company = saramin_company_parse((FX / "saramin_company.html").read_text(encoding="utf-8"))
    p = {"src": "saramin"}
    d = {"_detail": co.parse_detail(DET_TEXT)}
    assert co.quality_reason(p, d, company, {"due": date(2026, 12, 9), "kind": "date"}, TODAY) == ""
    assert "임베디드 리눅스" in d["_sections"]["담당 업무"]
    assert co.quality_reason(p, d, company, {"due": date(2026, 11, 24), "kind": "date"}, TODAY)
    assert co.quality_reason(p, d, None, {"due": date(2026, 12, 9), "kind": "date"}, TODAY)
    assert co.quality_reason(p, {"_detail": co.parse_detail(DET_IMG)}, company,
                             {"due": date(2026, 12, 9), "kind": "date"}, TODAY)


def test_unrelated_field_and_mechanical_roles_are_excluded():
    assert co.unrelated_role_reason("로봇 감속기 설계 엔지니어", "감속기 메커니즘 설계 및 도면 작성") == "기계·생산 직무"
    assert co.unrelated_role_reason("자동화 사업 현장 설치", "자동화 기계 현장 설치 및 기술영업") == "현장 서비스·설치 직무"
    assert co.unrelated_role_reason("로봇 제어 SW 엔지니어", "로봇용 액추에이터 제어 SW 개발") == ""


def test_recruiter_is_not_treated_as_hiring_company():
    assert co.recruiter_company_reason("닥터파인드", {"fields": {"업종": "고용 알선업"}})
    assert co.recruiter_company_reason("디퍼런트에이치알", {"fields": {"사업내용": "채용대행, 헤드헌팅"}})
    assert co.recruiter_company_reason("로봇제조사", {"fields": {"업종": "산업용 로봇 제조업"}}) == ""


def fake_fetch(pages):
    calls = []

    def f(url):
        calls.append(url)
        for k, v in pages.items():
            if k in url:
                return v
        return "<html></html>"
    f.calls = calls
    return f


def test_crawl_stops_at_known_and_dedupes_across_keywords():
    known = {"saramin:55660581", "saramin:55657889"}
    f = fake_fetch({"%EA%B0%81": LIST})            # 키워드 '각'(임의)
    r = co.crawl(["각", "각"], known, f, TODAY, stop_known=2, say=lambda *_: None)
    ids = [p["id"] for p in r["new"]]
    assert ids == ["55661373", "55660876"]
    first, second = r["per_keyword"]
    assert first["새 공고"] == 2 and first["이미 있음"] == 2 and "연속" in first["멈춘 이유"]
    assert second["새 공고"] == 0 and second["다른 키워드와 겹침"] == 2     # 두 번째 키워드에서는 겹침으로 셈


def test_crawl_blocked_stops_everything():
    def f(url):
        raise co.Blocked("HTTP 429")
    r = co.crawl(["a", "b"], set(), f, TODAY, say=lambda *_: None)
    assert r["blocked"] and len(r["per_keyword"]) == 1


def test_dry_run_saves_nothing(client):
    f = fake_fetch({"searchword=": LIST})
    out = co.run(["각"], apply=False, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True)
    assert out["새 공고(중복 뺀)"] == 4 and out["mode"].startswith("미리보기")
    assert out["등록일 분포"]["등록 2026-11-24"] == 3 and out["등록일 분포"]["수정일 표기(등록일 모름)"] == 1
    with SessionLocal() as db:
        assert db.execute(select(Job)).scalars().all() == []


def test_apply_saves_dates_and_is_idempotent(client):
    f = fake_fetch({"searchword=": LIST})
    out = co.run(["각"], apply=True, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True)
    assert out["저장한 새 공고"] == 4
    with SessionLocal() as db:
        by = {j.company: j for j in db.execute(select(Job)).scalars()}
    a = by["에이치알코어스"]
    assert a.raw["등록일"] == "2026-11-24" and a.raw["최초 수집"] == TODAY.isoformat() and a.first_seen == TODAY and a.posted_date == date(2026, 11, 24)
    assert a.raw["구분"] == "[신규·수집]" and a.raw["출처"] == "사람인 수집" and a.deadline_text == "12/9" and a.status == "접수중"
    assert "등록일" not in by["(주)미래컴퍼니"].raw and by["(주)미래컴퍼니"].raw["최초 수집"] == TODAY.isoformat()
    assert by["(주)엠피지오"].status == "접수중(채용시 마감)" and by["(주)룰루랩"].status == "상시(채용시 마감)"
    again = co.run(["각"], apply=True, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True)
    assert again["새 공고(중복 뺀)"] == 0                   # 두 번째는 모두 아는 공고
    with SessionLocal() as db:
        assert len(db.execute(select(Job)).scalars().all()) == 4


def test_details_save_text_and_use_start_date_when_posted_missing(client):
    pages = {"searchword=": LIST, "view-detail": DET_TEXT}
    out = co.run(["각"], apply=True, details=True, fetch=fake_fetch(pages), ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True)
    assert out["상세 페이지 읽음"] == 4
    with SessionLocal() as db:
        by = {j.company: j for j in db.execute(select(Job)).scalars()}
    m = by["(주)미래컴퍼니"]                              # 목록은 수정일 → 상세의 접수 시작일로 대신함
    assert m.raw["등록일"] == "2026-11-24" and "임베디드 리눅스" in m.raw["원문 본문"] and m.raw["이미지 공고"] is False


def ai_fake(urls, key):
    ai_fake.n += 1
    return {"data": {"필수": ["C++ 3년"], "우대": ["ROS2"], "필요역량": ["소통"], "기타안내": ["보훈 우대"]}, "model": "claude-sonnet-5-5", "tokens_in": 5000, "tokens_out": 800,
            "cost_usd": 0.018, "truncated": False}


def test_image_posting_ai_cap(client):
    ai_fake.n = 0
    pages = {"searchword=": LIST, "view-detail": DET_IMG}
    out = co.run(["각"], apply=True, details=True, ai_images=2, fetch=fake_fetch(pages), ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, ai_reader=ai_fake)
    assert ai_fake.n == 2 and out["AI로 읽은 이미지 공고"] == 2        # 하루 한도 2건
    with SessionLocal() as db:
        jobs = db.execute(select(Job)).scalars().all()
    read = [j for j in jobs if j.raw.get("AI 읽은 날") == TODAY.isoformat()]
    assert len(read) == 2 and read[0].raw["필수"] == "- C++ 3년" and "[기타안내]" in read[0].raw["이미지 공고 기타"]
    assert all(j.raw["이미지 공고"] for j in jobs)
    # 같은 날 다시 돌리면 이미 2건을 읽었으므로 더 읽지 않음(새 공고가 없어 요청도 없음)
    with SessionLocal() as db:
        assert co.ai_today(db, TODAY) == 2


def test_fetcher_blocks_and_limits():
    class R:
        def __init__(s, c): s.status_code, s.text = c, "ok"
        def raise_for_status(s): pass

    class C:
        def __init__(s, c): s.c = c
        def get(s, u): return R(s.c)
    with pytest.raises(co.Blocked):
        co.Fetcher(sleep=(0, 0), client=C(429)).get("http://x")
    f = co.Fetcher(sleep=(0, 0), max_requests=1, client=C(200))
    assert f.get("http://x") == "ok"
    with pytest.raises(co.Blocked):
        f.get("http://x")


def test_run_record_for_preview_and_apply(client):
    from app.models import CollectRun
    f = fake_fetch({"searchword=": LIST, "view-detail": DET_IMG})
    co.run(["각"], apply=False, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True)
    ai_fake.n = 0
    co.run(["각"], apply=True, details=True, ai_images=2, fetch=fake_fetch({"searchword=": LIST, "view-detail": DET_IMG}), ref=TODAY,
           say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, ai_reader=ai_fake)
    with SessionLocal() as db:
        runs = db.execute(select(CollectRun).order_by(CollectRun.id)).scalars().all()
    pre, real = runs
    assert pre.mode == "미리보기" and pre.status == "완료" and pre.requests == 2 and pre.new_found == 4 and pre.saved_new == 0 and pre.ai_model is None
    assert real.mode == "저장" and real.day == "2026-11-25" and real.finished_at is not None
    assert (real.requests, real.list_pages, real.detail_requests, real.detail_ok) == (6, 2, 4, 4)   # 목록 2쪽(가짜 사이트가 같은 쪽을 두 번 줌)
    assert (real.listed, real.known, real.new_found, real.saved_new) == (8, 0, 4, 4)
    assert (real.image_postings, real.ai_reads) == (4, 2) and real.ai_model == "claude-sonnet-5-5"
    assert (real.input_tokens, real.output_tokens) == (10000, 1600) and real.cost_usd == 0.036
    assert real.ai_settings["thinking"].startswith("끔") and real.ai_settings["조각 높이(px)"] == 900 and real.ai_settings["조각 겹침(px)"] == 150
    assert real.options == {"details": True, "ai_images": 2, "stop_known": 99, "max_pages": co.MAX_PAGES, "max_career": -1, "exclude_words": 0, "keep_grad": True, "sources": ["saramin"], "title_judge": True}
    assert any("한도" in n for n in real.notes) and real.per_keyword[0]["키워드"] == "각"


def test_run_record_on_block_and_error(client):
    from app.models import CollectRun

    def blocked(url):
        raise co.Blocked("HTTP 429")
    co.run(["a"], apply=True, fetch=blocked, ref=TODAY, say=lambda *_: None)

    def boom(url):
        raise RuntimeError("망가짐")
    with pytest.raises(RuntimeError):
        co.run(["a"], apply=True, fetch=boom, ref=TODAY, say=lambda *_: None)
    with SessionLocal() as db:
        a, b = db.execute(select(CollectRun).order_by(CollectRun.id)).scalars().all()
    assert a.status == "차단으로 중단" and "429" in a.blocked
    assert b.status == "오류" and "망가짐" in b.notes[0] and b.finished_at is not None


def test_admin_api_and_report(admin_client, capsys):
    co.run(["각"], apply=False, fetch=fake_fetch({"searchword=": LIST}), ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True)
    items = admin_client.get("/api/admin/collect-runs").json()["items"]
    assert len(items) == 1 and items[0]["new_found"] == 4 and "per_keyword" not in items[0]
    full = admin_client.get(f"/api/admin/collect-runs/{items[0]['id']}").json()
    assert full["per_keyword"][0]["새 공고"] == 4
    assert admin_client.get("/api/admin/collect-runs/9999").status_code == 404
    co.report(5)
    assert "미리보기·완료" in capsys.readouterr().out


def test_collect_runs_admin_only(client):
    assert client.get("/api/admin/collect-runs").status_code in (401, 403)


def test_over_cap_image_postings_are_read_on_later_runs(client):
    ai_fake.n = 0
    pages = {"searchword=": LIST, "view-detail": DET_IMG}
    co.run(["각"], apply=True, details=True, ai_images=1, fetch=fake_fetch(pages), ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, ai_reader=ai_fake)
    with SessionLocal() as db:
        jobs = db.execute(select(Job)).scalars().all()
        assert sum(1 for j in jobs if j.raw.get("AI 읽은 날")) == 1 and all(j.raw["이미지 주소"] for j in jobs)
    # 다음 날: 새 공고는 없고(다 아는 공고), 한도 2건 → 밀린 2건을 읽음. 상세 페이지를 다시 열지 않음(저장된 그림 주소 사용)
    f = fake_fetch(pages)
    out = co.run(["각"], apply=True, details=True, ai_images=2, fetch=f, ref=date(2026, 11, 26), say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, ai_reader=ai_fake)
    assert out["밀린 이미지 공고 처리"] == 2 and out["AI로 읽은 이미지 공고"] == 2 and not any("view-detail" in u for u in f.calls)
    with SessionLocal() as db:
        assert sum(1 for j in db.execute(select(Job)).scalars() if j.raw.get("AI 읽은 날")) == 3


def test_backlog_stops_retrying_after_failures(client):
    pages = {"searchword=": LIST, "view-detail": DET_IMG}
    co.run(["각"], apply=True, details=True, ai_images=0, fetch=fake_fetch(pages), ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True)

    def bad(urls, key):
        raise RuntimeError("AI 오류")
    for day in (26, 27, 28, 29):
        co.run(["각"], apply=True, details=True, ai_images=10, fetch=fake_fetch(pages), ref=date(2026, 11, day), say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, ai_reader=bad)
    with SessionLocal() as db:
        assert {j.raw["AI 시도"] for j in db.execute(select(Job)).scalars()} == {co.AI_TRIES}      # 3번만 시도하고 멈춤


def test_career_min():
    assert co.career_min("경력3년↑") == 3 and co.career_min("경력 10~17년") == 10 and co.career_min("경력 3~8년") == 3
    assert co.career_min("신입·경력") == 0 and co.career_min("경력무관") == 0 and co.career_min("신입") == 0 and co.career_min("경력") is None


def test_skip_reason_rules():
    base = {"title": "임베디드 개발자", "company": "A", "career": "경력3년↑", "edu": "대졸↑"}
    assert co.skip_reason(base) == ""
    assert "경력 5년 초과" in co.skip_reason({**base, "career": "경력10년↑"})
    assert co.skip_reason({**base, "career": "경력10년↑"}, max_career=-1) == ""
    assert "석사" in co.skip_reason({**base, "edu": "석사↑"}) and co.skip_reason({**base, "edu": "석사↑"}, keep_grad=True) == ""
    assert "강사" in co.skip_reason({**base, "title": "[학원강사] 코딩 강사"}, exclude=["강사"])
    assert co.skip_reason({**base, "career": "경력"}) == ""          # 읽을 수 없으면 거르지 않음


def test_filters_skip_and_count_in_run_and_record(client):
    from app.models import CollectRun
    f = fake_fetch({"searchword=": LIST})
    out = co.run(["각"], apply=True, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99)          # 기본 조건: 경력 5년 초과·석사 거름
    assert out["새 공고(중복 뺀)"] == 3 and sum(out["조건으로 건너뜀"].values()) == 1
    with SessionLocal() as db:
        names = {j.company for j in db.execute(select(Job)).scalars()}
        rec = db.execute(select(CollectRun)).scalars().first()
    assert "(주)미래컴퍼니" not in names and len(names) == 3 and rec.skipped == 1
    # 건너뛴 공고는 저장하지 않았으므로 다음 날에도 다시 보이지만 '새 공고'로 세지 않음
    out2 = co.run(["각"], apply=False, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99)
    assert out2["새 공고(중복 뺀)"] == 0


def test_skipped_count_toward_stop_rule():
    items = [{"id": str(i), "title": "t", "company": "c", "career": "경력10년↑", "edu": "", "loc": "", "emp": "", "due_raw": "", "day_kind": "", "day": ""} for i in range(1, 7)]
    html = '<div class="item_recruit" value="%s"><h2 class="job_tit"><a title="t">t</a></h2><div class="job_condition"><span>서울</span><span>경력10년↑</span></div></div>'
    page = "".join(html % i["id"] for i in items)
    f = fake_fetch({"searchword=": f"<html>{page}</html>"})
    r = co.crawl(["x"], set(), f, TODAY, stop_known=5, say=lambda *_: None, skip=lambda p: co.skip_reason(p))
    assert r["new"] == [] and r["per_keyword"][0]["조건으로 건너뜀"] == 6 and "연속" in r["per_keyword"][0]["멈춘 이유"]


def test_text_posting_requirements_saved_by_rule_parser(client):
    pages = {"searchword=": LIST, "view-detail": DET_TEXT}
    co.run(["각"], apply=True, details=True, fetch=fake_fetch(pages), ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True)
    with SessionLocal() as db:
        j = db.execute(select(Job).where(Job.company == "에이치알코어스")).scalar_one()
    assert "경력 3년 이상" in j.raw["필수"] and j.raw["필수·우대 출처"] == "규칙 파서"


# ───────── v13n: 연결 끊김 견디기 · 관심 회사 ─────────
def _it(i, company="셀트리온", title="분야별 수시채용"):
    return {"id": str(i), "title": title, "company": company, "loc": "", "career": "경력무관", "edu": "", "emp": "", "due_raw": "", "day_kind": "", "day": "", "src": "saramin"}


def test_fetcher_retries_dropped_connection(monkeypatch):
    import httpx
    monkeypatch.setattr(co.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class C:
        def get(self, url):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return httpx.Response(200, text="ok", request=httpx.Request("GET", url))
    f = co.Fetcher(sleep=(0, 0), client=C())
    assert f.get("http://x") == "ok" and calls["n"] == 3


def test_fetcher_gives_up_after_tries(monkeypatch):
    import httpx
    monkeypatch.setattr(co.time, "sleep", lambda s: None)

    class C:
        def get(self, url):
            raise httpx.RemoteProtocolError("x")
    with pytest.raises(httpx.RemoteProtocolError):
        co.Fetcher(sleep=(0, 0), client=C()).get("http://x")


def test_crawl_continues_after_network_error():
    import httpx
    seen = []

    def fetch(url):
        if "bad" in url:
            raise httpx.RemoteProtocolError("Server disconnected")
        return LIST
    r = co.crawl(["bad", "ROS"], set(), fetch, TODAY, max_pages=1, say=lambda *_: None, seen={})
    assert "네트워크 오류" in r["per_keyword"][0]["멈춘 이유"] and r["per_keyword"][1]["새 공고"] > 0 and not r["blocked"]


def test_watch_crawl_adds_company_postings_up_to_max(monkeypatch):
    def fake_crawl(kws, known, fetch, ref, stop_known, max_pages, say, skip=None, src=None, seen=None, skipped=None):
        for i in range(15):
            seen[f"saramin:{i}"] = _it(i)
        seen["saramin:99"] = _it(99, company="다른회사")
        return {"per_keyword": [], "new": [], "blocked": "", "skipped": skipped}
    monkeypatch.setattr(co, "crawl", fake_crawl)
    seen = {}
    r = co.watch_crawl(["셀트리온"], set(), None, TODAY, ["saramin"], seen, {}, None, say=lambda *_: None, max_new=10)
    assert len(seen) == 10 and all(p["_watch"] == "셀트리온" for p in seen.values()) and r["회사별"] == {"셀트리온": 10}
    assert "saramin:99" not in seen                                            # 회사명이 다르면 안 넣음


def test_cn_ignores_spaces_and_corp_marks():
    assert co._cn("㈜ LS ITC") == co._cn("(주)LS ITC") == "lsitc"


def test_apply_judge_keeps_watch_company_x_as_triangle():
    new = [{**_it(1), "_watch": "셀트리온"}, _it(2, company="일반회사")]
    ask = lambda *a, **k: None
    import json

    def fake_judge(target, key, model, ask=None):
        return {"result": [{"v": "×", "w": "직무 불명"}, {"v": "×", "w": "직무 불명"}], "failed": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    orig = co.tj.judge
    co.tj.judge = fake_judge
    try:
        keep, rel, drops, info = co.apply_judge(new, {}, "k", "m", 100)
    finally:
        co.tj.judge = orig
    assert [p["id"] for p in keep] == ["1"] and rel == {"saramin:1": "△"} and list(drops) == ["saramin:2"]


def test_to_sheet_row_carries_watch_mark():
    p = {**co.parse_list(LIST)[0], "_watch": "LS ITC", "src": "saramin"}
    assert co.to_sheet_row(p, TODAY)["_watch"] == "LS ITC"


def test_load_watch_skips_comments(tmp_path):
    f = tmp_path / "w.txt"
    f.write_text("# 설명\nLS ITC\n\n셀트리온\n", encoding="utf-8")
    assert co.load_watch(f) == ["LS ITC", "셀트리온"] and co.load_watch(tmp_path / "none.txt") == []


def test_run_with_watch_saves_company_posting_with_mark(client):
    # 일반 키워드 검색에는 아무것도 안 나오고, 관심 회사 이름 검색에서만 공고가 나오는 경우
    def f(url):
        return LIST if "%EC%97%90%EC%9D%B4%EC%B9%98" in url else "<html></html>"       # '에이치알코어스' 검색어(앞부분)에만 목록을 줌
    out = co.run(["각"], apply=True, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, judge=False, watch=["에이치알코어스"], watch_max=5)
    with SessionLocal() as db:
        by = {j.company: j for j in db.execute(select(Job)).scalars()}
    assert out["저장한 새 공고"] == 1 and by["에이치알코어스"].raw["관심기업"] == "에이치알코어스"


def test_watch_company_clear_non_sw_title_is_still_dropped():
    new = [{**_it(1), "_watch": "셀트리온"}, {**_it(2, title="뷰티 마케터"), "_watch": "셀트리온"}]

    def fake_judge(target, key, model, ask=None):
        return {"result": [{"v": "×", "w": "부문 불명확한 수시채용"}, {"v": "×", "w": "퍼포먼스 마케터, SW 무관"}], "failed": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    orig = co.tj.judge
    co.tj.judge = fake_judge
    try:
        keep, rel, drops, info = co.apply_judge(new, {}, "k", "m", 100)
    finally:
        co.tj.judge = orig
    assert [p["id"] for p in keep] == ["1"] and list(drops) == ["saramin:2"]


def test_ai_read_images_defaults_to_haiku_with_thinking_off(monkeypatch):
    import scripts.test_image_read as tir
    seen = {}
    monkeypatch.setattr(tir, "fetch_image", lambda u: (b"x", "image/png", 1))
    monkeypatch.setattr(tir, "slice_image", lambda d, m, h, o=40: ([(b"x", "image/png")], 1))

    def fake_call(provider, key, model, msgs, images, max_tokens, extra=None):
        seen.update(model=model, extra=extra)
        return {"text": "{}", "in": 100, "out": 10, "truncated": False}
    monkeypatch.setattr(co.ai_mod, "_call", fake_call)
    r = co.ai_read_images(["http://x/a.png"], "k")
    assert co.AI_MODEL == "claude-haiku-5-5" and seen["model"] == "claude-haiku-5-5" and seen["extra"] == {"thinking": {"type": "disabled"}} and r["model"] == "claude-haiku-5-5"


def test_ai_read_images_slices_finer_with_bigger_overlap(monkeypatch):
    import scripts.test_image_read as tir
    got = {}
    monkeypatch.setattr(tir, "fetch_image", lambda u: (b"x", "image/png", 1))

    def fake_slice(d, m, h, overlap=40, jpeg=0):
        got.update(h=h, overlap=overlap)
        return [(b"x", "image/png")], ""
    monkeypatch.setattr(tir, "slice_image", fake_slice)
    monkeypatch.setattr(co.ai_mod, "_call", lambda *a, **k: {"text": "{}", "in": 1, "out": 1, "truncated": False})
    co.ai_read_images(["http://x/a.png"], "k")
    assert (got["h"], got["overlap"]) == (900, 150)
    co.ai_read_images(["http://x/a.png"], "k", slice_h=600, overlap=40)
    assert (got["h"], got["overlap"]) == (600, 40)


def test_image_compare_report(monkeypatch, tmp_path):
    from PIL import Image
    import io as _io
    from scripts import image_compare as ic
    buf = _io.BytesIO(); Image.new("RGB", (800, 2000), "white").save(buf, "PNG")
    monkeypatch.setattr(ic, "fetch_image", lambda u: (buf.getvalue(), "image/png", {}))
    monkeypatch.setattr(ic, "pick", lambda n, s, i: [{"id": 1, "company": "A사", "title": "t", "urls": ["http://x/a.png"]}])

    def reader(urls, key, model, h, ov, extra=None):
        big = "sonnet" in model
        d = {"필수": ["파이썬 가능", "ROS2 경험"] if big else ["파이썬 가능"], "우대": ["영어"]}
        return {"data": d, "model": model, "tokens_in": 10000, "tokens_out": 4000, "images": 3, "cost_usd": 0.08 if big else 0.004, "truncated": False}
    out = tmp_path / "r.md"
    md = ic.run(1, configs=("S", "H0", "H1"), out=str(out), say=lambda *a: None, reader=reader)
    assert "예측 절약" in md and "실제 절약" in md and "ROS2 경험" in md
    assert out.exists() and out.with_suffix(".json").exists()


def test_ai_read_images_passes_explicit_extra(monkeypatch):
    import scripts.test_image_read as tir
    seen = []
    monkeypatch.setattr(tir, "fetch_image", lambda u: (b"x", "image/png", 1))
    monkeypatch.setattr(tir, "slice_image", lambda d, m, h, o=40: ([(b"x", "image/png")], ""))
    monkeypatch.setattr(co.ai_mod, "_call", lambda *a, **k: (seen.append(a[-1]), {"text": "{}", "in": 1, "out": 1, "truncated": False})[1])
    co.ai_read_images(["u"], "k", extra={"output_config": {"effort": "low"}})
    co.ai_read_images(["u"], "k", extra={})
    assert seen == [{"output_config": {"effort": "low"}}, {}]


def test_image_compare_ocr_modes(monkeypatch, tmp_path):
    from PIL import Image
    import io as _io
    from scripts import image_compare as ic
    buf = _io.BytesIO(); Image.new("RGB", (800, 2000), "white").save(buf, "PNG")
    monkeypatch.setattr(ic, "fetch_image", lambda u: (buf.getvalue(), "image/png", {}))
    monkeypatch.setattr(ic, "pick", lambda n, s, i: [{"id": 1, "company": "A사", "title": "t", "urls": ["http://x/a.png"]}])
    monkeypatch.setattr(ic, "structure_text", lambda text, key, model, extra: {"data": {"필수": ["파이썬 가능"], "우대": []}, "tokens_in": 500, "tokens_out": 300, "cost_usd": 0.001, "truncated": False})

    def reader(urls, key, model, h, ov, extra=None):
        return {"data": {"필수": ["파이썬 가능", "ROS2 경험"], "우대": ["영어"]}, "tokens_in": 9000, "tokens_out": 3000, "cost_usd": 0.05, "truncated": False}
    md = ic.run(1, configs=("S", "G5", "T", "TA"), out=str(tmp_path / "o.md"), say=lambda *a: None, reader=reader, ocr=lambda urls, h, ov: "자격요건 파이썬  가능 ROS2")
    assert "## 0. 한눈에" in md and "Tesseract만" in md and "$0.0000" in md
    # OCR 전문에 '파이썬 가능'은 있고 'ROS2 경험'은 80% 미만 → 필수 2개 중 1개 찾음
    assert ic.covered("파이썬 가능", "자격요건 파이썬  가능 ROS2") and not ic.covered("ROS2 경험", "자격요건 파이썬  가능 ROS2")
    assert ic.TEXT_PROMPT != ic.PROMPT and "OCR" in ic.TEXT_PROMPT


def test_ocr_text_real_tesseract(tmp_path, monkeypatch):
    import shutil
    if not shutil.which("tesseract"):
        import pytest; pytest.skip("tesseract 없음")
    from PIL import Image, ImageDraw, ImageFont
    import io as _io
    from scripts import image_compare as ic
    im = Image.new("RGB", (900, 140), "white"); ImageDraw.Draw(im).text((20, 40), "Python experience", fill="black", font=ImageFont.load_default(size=48))
    buf = _io.BytesIO(); im.save(buf, "PNG")
    monkeypatch.setattr(ic, "fetch_image", lambda u: (buf.getvalue(), "image/png", {}))
    assert "experience" in ic.ocr_text(["u"]).lower()


def test_image_compare_reuses_baseline(monkeypatch, tmp_path):
    from PIL import Image
    import io as _io, json as _json
    from scripts import image_compare as ic
    buf = _io.BytesIO(); Image.new("RGB", (800, 3000), "white").save(buf, "PNG")
    monkeypatch.setattr(ic, "fetch_image", lambda u: (buf.getvalue(), "image/png", {}))
    base = {"posts": [{"id": 1, "company": "A사", "title": "t", "urls": ["u"], "n_slices": {"S": 4}}], "pred": {"S": {"in": 5000, "out": 2600}},
            "act": {"S": {"in": 5000, "out": 2600, "cost": 0.04, "sec": 17.0, "fail": 0, "trunc": 0}}, "results": {"S": {"1": {"필수": ["파이썬 가능"], "우대": []}}}}
    bp = tmp_path / "b.json"; bp.write_text(_json.dumps(base, ensure_ascii=False), encoding="utf-8")
    calls = []

    def reader(urls, key, model, h, ov, extra=None):
        calls.append((model, h, ov))
        return {"data": {"필수": ["파이썬 가능"], "우대": []}, "tokens_in": 3000, "tokens_out": 900, "cost_usd": 0.001, "truncated": False}
    md = ic.run(configs=("S", "N1", "M1"), baseline=str(bp), say=lambda *a: None, reader=reader)
    assert [c[0] for c in calls] == ["claude-haiku-5-5", "claude-haiku-5-5"] and calls[0][1:] == (0, 0) and calls[1][1:] == (2576, 150)
    assert "$0.0400" in md or "0.0400" in md
    # 800x3000: 긴 변 2576으로 줄이면 687x2576 → 25*92=2300토큰 (한 장)
    assert ic.est_image_tokens([(buf.getvalue(), "image/png")]) == 2300
