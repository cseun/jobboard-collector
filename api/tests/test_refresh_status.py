from datetime import date

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import CollectRun, Job
from scripts import collect as co
from scripts import collect_sources as cs
from scripts import refresh_status as rs

TODAY = date(2026, 11, 25)
QUIET = lambda *_: None  # noqa: E731
SA = lambda due: f'<html><head><meta name="description" content="회사, 직무, 경력:경력무관, 학력:학력무관, 면접 후 결정, 마감일:{due}, 홈페이지:"></head><body>x</body></html>'  # noqa: E731
JK = lambda due: f'<html><head><meta property="og:description" content="경력 : 경력 , 학력 : 학력무관, 급여 : 회사 내규에 따름, 마감일 : {due}"/></head></html>'  # noqa: E731
WD = lambda status, due: '{"job":{"status":"%s","due_time":%s}}' % (status, f'"{due}"' if due else "null")  # noqa: E731


def add(db, key, company, status="접수중", due=None, raw=None):
    r = {"기업명": company, "접수 상태": status, "마감(KST)": f"{due.month}/{due.day}" if due else "상시", "최종 확인": "11/20", **(raw or {})}
    j = Job(source_key=key, company=company, title="t", status=status, deadline_text=r["마감(KST)"], deadline_date=due, raw=r)
    db.add(j)
    db.commit()
    return j.id


def http_error(code):
    req = httpx.Request("GET", "https://x")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


def test_status_parsers():
    assert cs.saramin_status(SA("2026-12-09")) == {"due": date(2026, 12, 9), "kind": "date"}
    assert cs.saramin_status(SA("채용시"))["kind"] == "채용시" and cs.saramin_status(SA("채용시"))["due"] is None
    assert cs.saramin_status("<html></html>") is None
    body = '<html><body>접수기간 : 2026-11-11 13시 ~ 2027-01-10 24시</body></html>'
    assert cs.saramin_status(body) == {"due": date(2027, 1, 10), "kind": "date"}
    assert cs.jobkorea_status(JK("2026.06.15")) == {"due": date(2026, 6, 15), "kind": "date"}
    assert cs.wanted_status(WD("close", None))["closed"] is True and cs.wanted_status(WD("active", "2026-11-30"))["due"] == date(2026, 11, 30)
    assert cs.wanted_status(WD("draft", None))["closed"] and cs.wanted_status("not json") is None


def test_free_close_by_deadline_and_no_requests(client):
    with SessionLocal() as db:
        past = add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "지남", due=date(2026, 11, 24))
        today = add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=2", "오늘까지", due=date(2026, 11, 25))
        always = add(db, "https://other.example/x", "상시", due=None)
    out = rs.run(apply=True, max_n=0, fetch=lambda u: 1 / 0, ref=TODAY, say=QUIET)
    assert out["날짜로 마감"] == 1
    with SessionLocal() as db:
        assert db.get(Job, past).status == "마감" and db.get(Job, past).raw["접수 상태"] == "마감" and "지남" in db.get(Job, past).raw["마감 사유"]
        assert db.get(Job, today).status == "접수중" and db.get(Job, always).status == "접수중"      # 마감일 당일은 아직 접수 중


def test_preview_changes_nothing(client):
    with SessionLocal() as db:
        jid = add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "지남", due=date(2026, 11, 1))
    out = rs.run(apply=False, fetch=lambda u: 1 / 0, ref=TODAY, say=QUIET)
    assert out["날짜로 마감"] == 1 and out["다시 확인할 공고"] == 1
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "접수중" and db.execute(select(CollectRun)).scalars().all() == []


def test_checks_each_site_and_updates(client):
    pages = {}

    def f(url):
        for k, v in pages.items():
            if k in url:
                if isinstance(v, int):
                    raise http_error(v)
                return v
        raise AssertionError(url)
    with SessionLocal() as db:
        ids = {
            "sa_ext": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=11", "사람인연장", due=date(2026, 11, 30)),
            "sa_gone": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=12", "사람인삭제", due=date(2026, 12, 20)),
            "sa_same": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=13", "사람인그대로", due=date(2026, 12, 9)),
            "sa_open": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=14", "사람인채용시", due=None),
            "jk_past": add(db, "https://www.jobkorea.co.kr/Recruit/GI_Read/21", "잡코리아지남", due=date(2027, 6, 15)),   # 연도를 잘못 짐작해 미래로 저장된 옛 공고
            "wd_close": add(db, "https://www.wanted.co.kr/wd/31", "원티드마감", due=None),
            "wd_due": add(db, "https://www.wanted.co.kr/wd/32", "원티드날짜", due=None),
            "sa_blank": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=15", "사람인읽기실패", due=date(2026, 12, 5)),
        }
    pages.update({"rec_idx=11": SA("2026-12-15"), "rec_idx=12": 404, "rec_idx=13": SA("2026-12-09"), "rec_idx=14": SA("채용시"),
                  "rec_idx=15": "<html>막힘 화면</html>", "GI_Read/21": JK("2026.06.15"), "jobs/31": WD("close", None), "jobs/32": WD("active", "2026-12-31")})
    out = rs.run(apply=True, fetch=f, ref=TODAY, say=QUIET)
    assert (out["다시 확인"], out["삭제·마감으로 바뀜"], out["마감일 갱신"], out["그대로"], out["읽지 못함"]) == (8, 3, 2, 2, 1)
    with SessionLocal() as db:
        g = {k: db.get(Job, v) for k, v in ids.items()}
        rec = db.execute(select(CollectRun)).scalars().one()
    assert g["sa_ext"].deadline_date == date(2026, 12, 15) and g["sa_ext"].deadline_text == "12/15" and g["sa_ext"].raw["마감(KST)"] == "12/15" and g["sa_ext"].status == "접수중"
    assert g["sa_gone"].status == "마감" and g["sa_gone"].raw["마감 사유"] == "사이트에서 삭제됨"
    assert g["jk_past"].status == "마감" and "6/15" in g["jk_past"].raw["마감 사유"]
    assert g["wd_close"].status == "마감" and g["wd_due"].deadline_date == date(2026, 12, 31)
    assert g["sa_open"].status == "접수중" and g["sa_open"].deadline_date is None                      # 채용시 마감은 날짜가 없으니 그대로
    assert all(g[k].raw["마감 확인일"] == TODAY.isoformat() for k in g)                                    # 읽기 실패한 것도 오늘 돌려 보냄
    assert g["sa_same"].raw["최종 확인"] == "11/20"                                                       # 최종 확인은 건드리지 않음
    assert rec.mode == "마감 갱신" and rec.status == "완료" and rec.requests == 8 and rec.updated == 5


def test_second_run_same_day_skips_checked_and_closed(client):
    calls = []
    with SessionLocal() as db:
        add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "a", due=date(2026, 12, 9))
    f = lambda u: calls.append(u) or SA("2026-12-09")  # noqa: E731
    rs.run(apply=True, fetch=f, ref=TODAY, say=QUIET)
    rs.run(apply=True, fetch=f, ref=TODAY, say=QUIET)
    assert len(calls) == 1


def test_limit_and_oldest_first(client):
    seen = []
    with SessionLocal() as db:
        add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "최근확인", due=date(2026, 12, 9), raw={"마감 확인일": "2026-11-24"})
        add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=2", "한번도", due=date(2026, 12, 9))
        add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=3", "더오래전", due=date(2026, 12, 9), raw={"마감 확인일": "2026-11-01"})
    rs.run(apply=True, max_n=2, fetch=lambda u: seen.append(u) or SA("2026-12-09"), ref=TODAY, say=QUIET)
    assert [u.split("=")[-1] for u in seen] == ["2", "3"]                      # 안 본 것 → 가장 오래된 것 순


def test_unknown_deadlines_before_dated_jobs_and_record_backlog(client):
    seen = []
    with SessionLocal() as db:
        add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "날짜있음", due=date(2026, 12, 9))
        unknown = add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=2", "미정", raw={"마감(KST)": "미정"})
        add(db, "https://other.example/jobs/3", "출처미지원", raw={"마감(KST)": "미정"})
        add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=4", "채용시", raw={"마감(KST)": "채용시"})
    out = rs.run(apply=True, max_n=1, fetch=lambda u: seen.append(u) or SA("2026-12-15"), ref=TODAY, say=QUIET)
    assert [u.split("=")[-1] for u in seen] == ["2"]
    assert (out["미정 마감 전체"], out["미정 마감 재점검 가능"], out["미정 마감 출처 미지원"], out["이번 회차 미정 마감"]) == (2, 1, 1, 1)
    with SessionLocal() as db:
        assert db.get(Job, unknown).deadline_date == date(2026, 12, 15)


def test_unknown_deadline_resolves_to_ongoing_when_source_says_so(client):
    with SessionLocal() as db:
        unknown = add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=8", "미정", raw={"마감(KST)": "미정"})
    rs.run(apply=True, max_n=1, fetch=lambda _: SA("채용시"), ref=TODAY, say=QUIET)
    with SessionLocal() as db:
        j = db.get(Job, unknown)
        assert j.raw["마감(KST)"] == "채용시" and j.deadline_text == "채용시"
        assert not rs.unknown_deadline(j)


def test_block_stops_only_that_site(client):
    seen = []
    with SessionLocal() as db:
        add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "사람인1", due=date(2026, 12, 9))
        add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=2", "사람인2", due=date(2026, 12, 9))
        add(db, "https://www.wanted.co.kr/wd/5", "원티드", due=None)

    def f(url):
        seen.append(url)
        if "saramin" in url:
            raise co.Blocked("HTTP 429")
        return WD("close", None)
    out = rs.run(apply=True, fetch=f, ref=TODAY, say=QUIET)
    assert len([u for u in seen if "saramin" in u]) == 1 and out["삭제·마감으로 바뀜"] == 1 and out["중단"].startswith("[saramin]")
    with SessionLocal() as db:
        assert db.execute(select(CollectRun)).scalars().one().status == "차단으로 중단"


def test_cli_rejects_unknown_source():
    import pytest
    with pytest.raises(SystemExit):
        rs.main(["--source", "indeed"])
