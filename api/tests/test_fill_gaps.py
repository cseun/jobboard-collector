from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CollectRun, Job
from scripts import collect as co
from scripts import fill_gaps as fg

FX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 25)
QUIET = lambda *_: None  # noqa: E731
BODY = ("📋 주요업무\n- 로봇 내비게이션 개발\n📋 자격요건\n- C++ 3년 이상\n- ROS2 경험\n📋 우대사항\n- Nav2 경험\n"
        "📋 근무조건\n연봉 4,000~5,000만원\n근무지: 서울 강남구 테헤란로 1\n" + "추가 안내 문구를 길게 적어 둡니다. " * 10)
BODY_NO_HEADS = "저희는 로봇을 만듭니다. 연봉은 면접 후 협의하며 서울 판교에서 근무합니다. 전형은 서류 후 면접 두 번입니다. " * 4


def add(db, key, company, raw_extra=None, status="접수중"):
    raw = {"기업명": company, "공고명·담당 업무": "로봇 SW", "원문 URL": key, "접수 상태": status, "필수": "", "우대": "", "근무 위치": "미확인"}
    raw.update(raw_extra or {})
    j = Job(source_key=key, company=company, title="로봇 SW", status=status, raw=raw)
    db.add(j)
    db.commit()
    return j.id


def fake_ai(calls):
    def ai(text, blanks, key):
        calls.append(blanks)
        data = {"필수": "- 판교 근무 가능자", "우대": "없음", "급여(공고)": "면접 후 협의", "전형 절차": "서류 → 면접 2회", "근무 위치": "서울 판교"}
        return {"data": {b: data[b] for b in blanks}, "model": "claude-sonnet-5-5", "tokens_in": 1000, "tokens_out": 200, "cost_usd": 0.006}
    return ai


def test_pick_targets_filters(client):
    with SessionLocal() as db:
        ids = {
            "ok": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "채움대상"),
            "closed": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=2", "마감", status="마감"),
            "image": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=3", "이미지", {"이미지 공고": True}),
            "wanted": add(db, "https://www.wanted.co.kr/wd/4", "원티드"),
            "tried": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=5", "두번시도", {"채움 시도": 2}),
            "today": add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=6", "오늘시도", {"채움 시도": 1, "채움 시도일": TODAY.isoformat()}),
            "full": add(db, "https://www.jobkorea.co.kr/Recruit/GI_Read/7", "다참", {"필수": "x", "우대": "y", "근무 위치": "서울", "급여(공고)": "a", "전형 절차": "b"}),
            "jk": add(db, "https://www.jobkorea.co.kr/Recruit/GI_Read/8", "잡코리아"),
        }
        got = [j.id for j in fg.pick_targets(db, ref=TODAY)]
    assert got == [ids["jk"], ids["ok"]]                      # 최신(번호 큰 것) 먼저


def test_rule_parser_first_then_ai_only_for_rest(client):
    calls = []
    with SessionLocal() as db:
        jid = add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "채움대상", {"원문 본문": BODY})
    out = fg.run(apply=True, ai=fake_ai(calls), fetch=lambda u: "", ref=TODAY, say=QUIET)
    with SessionLocal() as db:
        j = db.get(Job, jid)
    assert "C++ 3년" in j.raw["필수"] and "Nav2" in j.raw["우대"]            # 규칙 파서가 먼저 채움
    assert calls == [["급여(공고)", "전형 절차", "근무 위치"]]                  # AI에는 규칙이 못 채운 칸만 보냄(필수·우대는 안 보냄)
    assert j.raw["급여(공고)"] == "면접 후 협의" and j.location == "서울 판교"
    assert j.raw["채움 기록"]["필수"].startswith("규칙 파서") and j.raw["채움 기록"]["근무 위치"].startswith("서버 AI claude-sonnet-5-5")
    assert j.raw["채움 시도"] == 1 and j.raw["채움 시도일"] == TODAY.isoformat()
    assert out["채운 공고"] == 1 and out["AI 사용"]["calls"] == 1


def test_ai_none_means_dash_and_existing_values_kept(client):
    with SessionLocal() as db:
        jid = add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "채움", {"원문 본문": BODY_NO_HEADS, "필수": "- 기존 값"})
    fg.run(apply=True, ai=fake_ai([]), fetch=lambda u: "", ref=TODAY, say=QUIET)
    with SessionLocal() as db:
        j = db.get(Job, jid)
    assert j.raw["필수"] == "- 기존 값" and j.raw["우대"] == "—"             # 기존 값은 그대로, '없음'은 —로(빈칸이 아님)
    assert "필수" not in j.raw["채움 기록"]


def test_preview_calls_nothing_and_saves_nothing(client):
    calls = []
    with SessionLocal() as db:
        jid = add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "채움", {"원문 본문": BODY})
    out = fg.run(apply=False, ai=fake_ai(calls), fetch=lambda u: 1 / 0, ref=TODAY, say=QUIET)
    assert out["대상"] == 1 and calls == [] and "채움 시도" not in (out.get("AI 사용") or {})
    with SessionLocal() as db:
        assert "채움 시도" not in db.get(Job, jid).raw and db.execute(select(CollectRun)).scalars().all() == []


def test_jobkorea_fetches_detail_and_iframe_and_records_run(client):
    pages = []
    main = '<html><body><iframe src="/Recruit/GI_Read_Comt_Ifrm?Gno=8"></iframe></body></html>'
    frame = "<html><body>" + "".join(f"<p>{ln}</p>" for ln in BODY.split("\n")) + "</body></html>"

    def f(url):
        pages.append(url)
        return frame if "Ifrm" in url else main
    with SessionLocal() as db:
        jid = add(db, "https://www.jobkorea.co.kr/Recruit/GI_Read/8", "잡코리아")
    out = fg.run(apply=True, ai=fake_ai([]), fetch=f, ref=TODAY, say=QUIET)
    with SessionLocal() as db:
        j, rec = db.get(Job, jid), db.execute(select(CollectRun)).scalars().one()
    assert len(pages) == 2 and "ROS2" in j.raw["필수"] and out["채운 공고"] == 1
    assert rec.mode == "빈칸 채우기" and rec.status == "완료" and rec.ai_model == "claude-sonnet-5-5" and (rec.input_tokens, rec.output_tokens) == (1000, 200) and rec.updated == 1


def test_unreadable_page_counts_a_try_and_continues(client):
    with SessionLocal() as db:
        a = add(db, "https://www.jobkorea.co.kr/Recruit/GI_Read/9", "읽기실패")
        b = add(db, "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "정상", {"원문 본문": BODY})
    out = fg.run(apply=True, ai=fake_ai([]), fetch=lambda u: "<html><body>짧음</body></html>", ref=TODAY, say=QUIET)
    with SessionLocal() as db:
        assert db.get(Job, a).raw["채움 시도"] == 1 and "필수" not in (db.get(Job, a).raw.get("채움 기록") or {})
        assert "C++" in db.get(Job, b).raw["필수"]
    assert out["채운 공고"] == 1 and any("읽지 못함" in x for x in out["건너뜀"])


def test_blocked_stops_and_records(client):
    def f(url):
        raise co.Blocked("HTTP 429")
    with SessionLocal() as db:
        add(db, "https://www.jobkorea.co.kr/Recruit/GI_Read/9", "막힘")
    out = fg.run(apply=True, ai=fake_ai([]), fetch=f, ref=TODAY, say=QUIET)
    with SessionLocal() as db:
        rec = db.execute(select(CollectRun)).scalars().one()
    assert out["중단"] and rec.status == "차단으로 중단" and rec.blocked


def test_cli_rejects_unknown_field():
    import pytest
    with pytest.raises(SystemExit):
        fg.main(["--fields", "기업명"])
