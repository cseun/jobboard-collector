import json
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Document, Job
from scripts import cleanup_excluded as ce
from scripts import collect as co
from scripts import judge_compare as jc
from scripts import title_judge as tj
from tests.test_title_judge import ask_by_title

FX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 25)
LIST_HTML = '''<div class="item_recruit" id="rec-%(id)s" value="%(id)s"><div class="area_job"><h2 class="job_tit"><a href="/zf_user/jobs/relay/view?rec_idx=%(id)s" title="%(title)s"><span>%(title)s</span></a></h2>
<div class="job_condition"><span><a>서울</a></span><span>경력무관</span><span>대졸↑</span><span>정규직</span></div><div class="job_date"><span class="date">~ 12/09(수)</span><span class="job_day">등록일 26/11/24</span></div></div>
<div class="area_corp"><strong class="corp_name"><a href="/x">%(co)s</a></strong></div></div>'''


def test_senior_words_are_not_hard_excluded_anymore():
    words = co.load_exclude()
    for t in ["AI Agent Engineer (팀장/팀원)", "펌웨어 개발(책임 연구원/과장급)", "임베디드 팀장급 모집"]:
        assert co.skip_reason({"title": t, "company": "A", "career": "경력무관", "edu": ""}, 5, words, False) == "", t       # 상세를 읽고 정함
    assert co.senior_hit({"title": "AI Agent Engineer (팀장/팀원)", "company": "A"}, co.load_senior()) == "팀장"
    assert co.skip_reason({"title": "로봇 사업 본부장", "company": "A", "career": "경력무관", "edu": ""}, 5, words, False)       # 순수 관리직은 바로 제외


def test_judge_senior_fails_open():
    items = [{"company": "A", "title": "팀장", "text": "x" * 200}] * 2
    bad = tj.judge_senior(items, "k", ask=lambda p: {"text": "몰라요", "in": 1, "out": 1})
    assert [r["junior"] for r in bad["result"]] == [True, True] and bad["failed"] == 1
    ok = tj.judge_senior(items, "k", ask=lambda p: {"text": json.dumps({"r": [{"i": 0, "junior": False, "w": "책임급만"}, {"i": 1, "junior": True, "w": "팀원 포함"}]}), "in": 10, "out": 5})
    assert [r["junior"] for r in ok["result"]] == [False, True]


def test_collect_checks_detail_for_senior_titles(client):
    def page(*rows):
        return "<html><body>" + "".join(LIST_HTML % r for r in rows) + "</body></html>"
    listing = page({"id": "111111", "title": "로봇 SW 개발 팀장급", "co": "책임사"}, {"id": "222222", "title": "임베디드 개발자 (팀장/팀원)", "co": "팀원사"},
                   {"id": "333333", "title": "PLC 제어 개발", "co": "일반사"})
    body = "<div class='user_content'>" + ("상세 본문 " * 60) + "</div>"

    def f(url):
        if "searchword=" in url:
            return listing if "&recruitPage=1" in url else "<html></html>"
        return body

    def ask(prompt):                                       # 직급 확인 프롬프트면 첫 공고(책임사)만 책임급만, 나머지는 팀원도
        if "junior" in prompt:
            n = prompt.count("회사: ")
            rows = [{"i": i, "junior": ("책임사" not in prompt.split(f"[{i}] 회사: ")[1].split("\n")[0]), "w": "테스트"} for i in range(n)]
            return {"text": json.dumps({"r": rows}, ensure_ascii=False), "in": 10, "out": 5}
        return ask_by_title({}, default="○")(prompt)

    pre = co.run(["각"], apply=False, details=True, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_pages=1, judge_ask=ask)
    assert pre["직급 확인 대상"] == 2
    out = co.run(["각"], apply=True, details=True, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_pages=1, judge_ask=ask)
    assert out["직급 확인"]["책임급만"] == 1 and out["직급 확인"]["팀원도 뽑음"] == 1 and out["저장한 새 공고"] == 2
    with SessionLocal() as db:
        by = {j.company: j for j in db.execute(select(Job)).scalars()}
        assert set(by) == {"팀원사", "일반사"} and by["팀원사"].raw["직급 확인"].startswith("상세 확인")
        assert "직급 확인" not in by["일반사"].raw
        assert "wanted" not in "".join(co.drop_cache(db)) and any("직급 확인" in v for v in co.drop_cache(db).values())      # 책임급만 → 기억해서 다시 안 봄


def test_import_listing_senior_marked_unconfirmed(client):
    items = [{"id": "7001", "t": "AI 엔지니어 (팀장/팀원)", "c": "A", "f": 0, "to": 3}, {"id": "7002", "t": "로봇 SW", "c": "B", "f": 0, "to": 3}]
    out = co.import_listing("wanted", [], apply=True, ref=TODAY, exclude=[], max_career=-1, judge=False, compact=items)
    assert out["직급 확인 대상(상세 미확인으로 △)"] == 1 and out["저장한 새 공고"] == 2
    with SessionLocal() as db:
        by = {j.company: j for j in db.execute(select(Job)).scalars()}
    assert by["A"].raw["관련도"] == "△" and "미확인" in by["A"].raw["직급 확인"] and "직급 확인" not in by["B"].raw


def test_cleanup_x_and_career(client):
    with SessionLocal() as db:
        for i, (t, rel, car) in enumerate([("로봇 SW", "○", "신입"), ("UX 디자이너", "×", "신입"), ("임베디드", "△", "경력 7년↑"), ("PLC", "", "경력 3~10년")]):
            db.add(Job(source_key=f"https://x/{i}", company=f"C{i}", title=t, raw={"구분": "[신규·수집]", "관련도": rel, "신입·경력": car}))
        db.commit()
    r = ce.run(False, [], use_x=True, max_career=5)
    assert r["지울 공고"] == 2 and set(r["이유별"]) == {"AI 판정 ×", "경력 7년 이상 요구"}
    assert ce.run(False, [])["지울 공고"] == 0                                           # 옵션 없이는 단어만
    ce.run(True, [], use_x=True, max_career=5)
    with SessionLocal() as db:
        assert {j.company for j in db.execute(select(Job)).scalars()} == {"C0", "C3"}     # 경력 3~10년(최소 3)은 남음


def test_judge_jev_and_compare():
    seen = []

    def post(body):
        seen.append(body)
        n = len(body["questions"])
        lines = body["state"].split("[공고 목록: 번호 / 회사 / 제목]\n")[1].splitlines()
        ans = {}
        for i in range(n):
            t = lines[i].split("\t")[2]
            ans[f"q{i}"] = {"type": "choice", "choice": "X" if "디자이너" in t else "O", "confidence": 0.9, "probabilities": {}}
        return {"model": "jev-1", "answers": ans, "usage": {"input_tokens": 1000, "output_tokens": 0}}
    items = [{"company": "A", "title": f"로봇 {i}"} for i in range(25)] + [{"company": "B", "title": "UX 디자이너"}]
    r = tj.judge_jev(items, "key", post=post, batch=20)
    assert len(seen) == 2 and r["result"][-1]["v"] == "×" and r["result"][0]["v"] == "○" and r["tokens_in"] == 2000 and abs(r["cost_usd"] - 2000 * 0.042 / 1e6) < 1e-9
    assert r["failed"] == 0 and tj.judge_jev(items[:2], "k", post=lambda b: (_ for _ in ()).throw(RuntimeError("x")))["failed"] == 1

    samples = jc.load_samples()
    assert len(samples) >= 40 and {s["expected"] for s in samples} == {"○", "△", "×", "필"}
    ask = ask_by_title({"디자이너": "×", "기획": "×"}, default="○")
    out = jc.compare(samples, ["haiku", "sonnet"], "k", None, ask=ask)
    assert set(out["모델"]) == {"haiku", "sonnet"} and "정답과 일치" in out["모델"]["haiku"] and out["모델"]["sonnet"]["haiku와 일치"].startswith(f"{len(samples)}/")


def test_judge_reports_truncation_reason_and_defaults():
    assert tj.MAX_TOKENS >= 8000 and tj.BATCH <= 30
    r = tj.judge([{"company": "A", "title": "로봇"}], "k", ask=lambda p: {"text": '{"r":[{"i":0,"v":"○"', "in": 1, "out": 8000, "truncated": True})
    assert r["failed"] == 1 and "잘림" in r["실패 이유"] and r["result"][0]["v"] == "△"


def test_profile_has_repair_and_llm_rules():
    assert "수리·A/S" in tj.PROFILE and "필드엔지니어" in tj.PROFILE and "LLM·에이전트·RAG" in tj.PROFILE and "도메인 무관" in tj.PROFILE


def test_profile_domain_is_not_a_reason_for_x():
    for w in ["산업(의료·금융", "SW QA·QC", "머신비전·광학(렌즈·조명·카메라) 쪽", "PM·컨설턴트·사업관리는 △"]:
        assert w in tj.PROFILE, w


def test_profile_mechanical_and_fae_are_x():
    assert "감속기·액추에이터" in tj.PROFILE and "FAE(Field Application Engineer)" in tj.PROFILE and "머신비전·광학(렌즈·조명·카메라) 쪽" in tj.PROFILE


def test_tag_relevance_groups_field_and_restores():
    raw = {"직무 분류": ["로봇 SW(제어·ROS)"], "분야": ["🤖 로봇"]}
    tj.tag_relevance(raw, "필")
    assert raw["관련도"] == "필" and raw["직무 분류"] == ["필드엔지니어"] and raw["직무 분류 원본"] == ["로봇 SW(제어·ROS)"]
    tj.tag_relevance(raw, "필")                                  # 두 번 해도 원본이 덮이지 않음
    assert raw["직무 분류 원본"] == ["로봇 SW(제어·ROS)"]
    tj.tag_relevance(raw, "△")                                   # 다시 판정해 필이 아니면 원래대로
    assert raw["직무 분류"] == ["로봇 SW(제어·ROS)"] and "직무 분류 원본" not in raw and raw["관련도"] == "△"


def test_judge_field_verdict_and_existing_apply(client):
    from scripts import judge_existing as je
    from tests.test_title_judge import ask_by_title
    with SessionLocal() as db:
        db.add(Job(source_key="https://x/f1", company="F", title="로봇 필드엔지니어", raw={"구분": "[신규·수집]", "직무 분류": ["기타 SW"]}))
        db.add(Job(source_key="https://x/f2", company="G", title="로봇 SW", raw={"구분": "[신규·수집]", "직무 분류": ["로봇 SW(제어·ROS)"]}))
        db.commit()
    r = je.run(True, ask=ask_by_title({"필드": "필"}, default="○"))
    assert r["필(필드엔지니어)"] == 1 and r["○"] == 1 and len(r["필 목록(최대 80)"]) == 1
    with SessionLocal() as db:
        got = {j.company: j.raw for j in db.execute(select(Job)).scalars()}
    assert got["F"]["직무 분류"] == ["필드엔지니어"] and got["F"]["관련도"] == "필" and got["G"]["직무 분류"] == ["로봇 SW(제어·ROS)"]
    assert je.run(True, redo=True, ask=ask_by_title({}, default="○"))["필(필드엔지니어)"] == 0
    with SessionLocal() as db:
        assert {j.company: j.raw["직무 분류"] for j in db.execute(select(Job)).scalars()}["F"] == ["기타 SW"]       # 다시 판정하면 원래 직무로 복원
