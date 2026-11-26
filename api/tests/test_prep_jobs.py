from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CollectRun, Document, Job, SharedData
from scripts import collect as co
from scripts import prep_jobs as pj

TODAY = date(2026, 11, 25)
QUIET = lambda *_: None  # noqa: E731
BODY = "담당업무: 장비 제어 소프트웨어 개발. 자격요건: 컴퓨터공학 또는 유사 전공, C# 가능자. 우대사항: ROS2 경험. 전형: 서류 → 면접. 지원: 회사 채용 홈페이지에서 접수. 마감 11/30 17:00. " * 2
AI_OK = {"summary": "회사 홈페이지 접수 · 11/30 17:00 마감",
         "jd": {"dept": "SW팀", "summary": "장비 제어 SW", "role": ["장비 제어 SW 개발"], "req": "- C# 가능자\n- 컴퓨터공학 전공", "plus": ["ROS2"]},
         "apply": {"url": "https://careers.example.com/apply", "steps": ["홈페이지 접수", "서류 → 면접"]},
         "q": [{"q": "지원 동기를 쓰세요", "limit": 700}], "tips": ["C# 경험 강조"], "newsPick": [{"i": 0, "s": "신제품 공개"}, {"i": 9, "s": "없는 번호"}]}


def add_job(db, n=1, company="(주)유일회사", raw=None):
    db.add(Job(source_key=f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={n}", company=company, title="SW 개발", status="접수중", deadline_text="11/30 17:00",
               raw={"기업명": company, "csn": "C1", "원문 본문": BODY, **(raw or {})}))


def add_req(db, key="sr1", **kw):
    db.add(Document(collection="prep", doc_id=key, value={"key": key, "company": "(주)유일회사", "title": "SW 개발", "url": "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1", "status": "요청됨", **kw}))


def fake_ai(res=AI_OK):
    calls = []

    def f(prompt, key):
        calls.append(prompt)
        return {"data": res, "model": "claude-sonnet-5-5", "tokens_in": 1000, "tokens_out": 500, "cost_usd": 0.01}
    f.calls = calls
    return f


def test_preview_does_not_call_ai_or_save(client):
    with SessionLocal() as db:
        add_job(db)
        add_req(db)
        db.commit()
    ai = fake_ai()
    out = pj.run(apply=False, ai=ai, fetch=lambda u: 1 / 0, ref=TODAY, say=QUIET)
    assert out["대상"] == 1 and "유일회사" in out["대상 예시"][0] and not ai.calls
    with SessionLocal() as db:
        assert db.get(SharedData, "prep") is None and db.execute(select(CollectRun)).first() is None


def test_apply_saves_docs_news_and_marks_request(client):
    with SessionLocal() as db:
        add_job(db)
        add_req(db)
        db.add(SharedData(key="companies", value={"C1": {"sum": ["8 명 사원수"], "det": ["업종 장비"], "intro": ["우리는 장비를 만듭니다"], "wel": []}}))
        db.add(SharedData(key="companyNews", value={"유일회사": {"updated": "2026-11-20", "items": [{"d": "2025-11-03", "t": "신제품 공개", "src": "한국일보", "u": "https://n.example/1"}]}}))
        db.add(SharedData(key="admin:docs", value={"유일회사": {"tips": ["내가 쓴 팁"], "apply": {"url": "https://mine.example"}}}))     # 직접 쓴 칸은 보존
        db.commit()
    ai = fake_ai()
    out = pj.run(apply=True, ai=ai, fetch=lambda u: 1 / 0, ref=TODAY, say=QUIET)
    assert out["정리함"] == 1 and out["실패"] == 0 and out["AI 사용"]["calls"] == 1
    assert "우리는 장비를 만듭니다" in ai.calls[0] and "신제품 공개" in ai.calls[0] and "ROS2" in ai.calls[0]
    with SessionLocal() as db:
        docs = db.get(SharedData, "admin:docs").value["유일회사"]
        assert docs["tips"] == ["내가 쓴 팁"] and docs["apply"]["url"] == "https://mine.example"          # 덮어쓰지 않음
        assert docs["apply"]["steps"] == ["홈페이지 접수", "서류 → 면접"] and docs["jd"]["req"] == ["C# 가능자", "컴퓨터공학 전공"]    # 비어 있던 칸만 채움
        assert docs["q"] == [{"q": "지원 동기를 쓰세요", "limit": 700}] and "서버 AI" in docs["qsrc"]
        assert docs["news"] == [{"d": "2025-11-03", "t": "신제품 공개", "s": "신제품 공개", "src": "한국일보", "u": "https://n.example/1"}]     # 없는 번호(9)는 버림
        prep = db.get(SharedData, "prep").value["sr1"]
        assert prep["auto"] is True and prep["summary"].startswith("🤖 서버 자동 정리") and prep["todo"]
        req = db.get(Document, ("prep", "sr1")).value
        assert req["status"] == "서버 처리 완료" and "jd" in req["채운 칸"] and req["todo"]
        run = db.execute(select(CollectRun)).scalars().all()[-1]
        assert run.mode == "지원 준비" and run.ai_reads == 1 and run.input_tokens == 1000 and run.cost_usd == 0.01
    again = pj.run(apply=True, ai=fake_ai(), fetch=lambda u: 1 / 0, ref=TODAY, say=QUIET)       # 이미 정리된 요청은 다시 하지 않음
    assert again["대상"] == 0


def test_failures_are_recorded_and_capped(client):
    with SessionLocal() as db:
        add_job(db, raw={"원문 본문": "", "이미지 공고": True})                    # 본문도 저장된 칸도 없음
        add_req(db)
        add_req(db, key="co-없는회사", company="없는회사", url="")
        db.commit()
    out = pj.run(apply=True, ai=fake_ai(), fetch=lambda u: "<html></html>", ref=TODAY, say=QUIET)
    assert out["정리함"] == 0 and out["실패"] == 2 and any("찾지 못함" in x for x in out["건너뜀"])
    with SessionLocal() as db:
        assert db.get(Document, ("prep", "sr1")).value["status"] == "서버 처리 실패"
        assert db.get(Document, ("prep", "sr1")).value["serverTries"] == 1
    pj.run(apply=True, ai=fake_ai(), fetch=lambda u: "<html></html>", ref=TODAY, say=QUIET)
    third = pj.run(apply=True, ai=fake_ai(), fetch=lambda u: "<html></html>", ref=TODAY, say=QUIET)
    assert third["대상"] == 0                                                      # 2번 실패하면 더 시도하지 않음


def test_empty_ai_answer_is_a_failure_not_a_fake_success(client):
    with SessionLocal() as db:
        add_job(db)
        add_req(db)
        db.commit()
    out = pj.run(apply=True, ai=fake_ai({"summary": "", "jd": {}, "apply": {}, "q": [], "tips": [], "newsPick": []}), ref=TODAY, say=QUIET)
    assert out["정리함"] == 0 and out["실패"] == 1
    with SessionLocal() as db:
        assert db.get(SharedData, "prep") is None
