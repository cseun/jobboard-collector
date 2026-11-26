import json
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Document, Job
from scripts import collect as co
from scripts import title_judge as tj

FX = Path(__file__).parent / "fixtures"
LIST = (FX / "saramin_list.html").read_text(encoding="utf-8")
WANTED = (FX / "wanted_list.json").read_text(encoding="utf-8")
TODAY = date(2026, 11, 25)


def ask_by_title(rules, default="○", calls=None):
    """프롬프트의 '번호\t회사\t제목' 줄을 읽어 rules(제목에 든 글자 → 판정)로 답하는 가짜 모델."""
    def ask(prompt):
        if calls is not None:
            calls.append(prompt)
        rows = []
        for ln in prompt.split("[공고 목록: 번호 / 회사 / 제목]\n")[1].splitlines():
            i, _c, t = ln.split("\t")
            v = next((v for w, v in rules.items() if w in t), default)
            rows.append({"i": int(i), "v": v, "w": "테스트"})
        return {"text": json.dumps({"r": rows}, ensure_ascii=False), "in": 1000, "out": 300}
    return ask


def test_judge_maps_verdicts_and_fails_open():
    items = [{"company": "A", "title": "로봇 SW"}, {"company": "B", "title": "UX 디자이너"}, {"company": "C", "title": "부문별 인재채용"}]
    r = tj.judge(items, "k", ask=ask_by_title({"디자이너": "×", "인재채용": "△"}))
    assert [x["v"] for x in r["result"]] == ["○", "×", "△"] and r["failed"] == 0 and r["cost_usd"] > 0
    bad = tj.judge(items, "k", ask=lambda p: {"text": "죄송하지만 판단할 수 없습니다", "in": 10, "out": 5})
    assert [x["v"] for x in bad["result"]] == ["△", "△", "△"] and bad["failed"] == 1       # 못 읽으면 전부 남김

    def boom(p):
        raise RuntimeError("api down")
    assert [x["v"] for x in tj.judge(items, "k", ask=boom)["result"]] == ["△", "△", "△"]
    odd = tj.judge(items[:1], "k", ask=lambda p: {"text": '{"r":[{"i":0,"v":"X","w":"x"}]}', "in": 1, "out": 1})
    assert odd["result"][0]["v"] == "×"                                                       # X도 ×로 읽음


def test_judge_batches():
    items = [{"company": "A", "title": f"공고{i}"} for i in range(130)]
    calls = []
    r = tj.judge(items, "k", ask=ask_by_title({}, calls=calls), batch=60)
    assert len(calls) == 3 and len(r["result"]) == 130 and all(x["v"] == "○" for x in r["result"])


def test_run_preview_judges_only_when_asked_and_saves_nothing(client):
    f = lambda url: LIST if "searchword=" in url else "<html></html>"
    ask = ask_by_title({"임베디드": "○", "하드웨어": "×"}, default="△")
    off = co.run(["각"], apply=False, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, judge_ask=ask)
    assert "AI 제목 판정" not in off and off["새 공고(중복 뺀)"] == 4                          # 미리보기 기본은 AI를 쓰지 않음
    on = co.run(["각"], apply=False, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, judge=True, judge_ask=ask)
    assert on["AI 제목 판정"]["판정한 공고"] == 4 and on["AI 제목 판정"]["×"] >= 1 and on["새 공고(중복 뺀)"] == 4 - on["AI 제목 판정"]["×"]
    assert on["조건으로 건너뜀"].get("AI 제목 판정 ×") == on["AI 제목 판정"]["×"]
    with SessionLocal() as db:
        assert db.execute(select(Job)).scalars().all() == [] and db.get(Document, (co.DROP_COLL, co.DROP_ID)) is None   # 캐시도 안 씀


def test_apply_drops_x_remembers_it_and_marks_relevance(client):
    f = lambda url: LIST if "searchword=" in url else "<html></html>"
    ask = ask_by_title({"임베디드": "○", "하드웨어": "×"}, default="△")
    out = co.run(["각"], apply=True, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, judge_ask=ask)
    n_x = out["AI 제목 판정"]["×"]
    assert n_x >= 1 and out["저장한 새 공고"] == 4 - n_x
    with SessionLocal() as db:
        jobs = db.execute(select(Job)).scalars().all()
        assert len(jobs) == 4 - n_x and {j.raw["관련도"] for j in jobs} <= {"○", "△"}
        assert len(co.drop_cache(db)) == n_x
    calls = []
    again = co.run(["각"], apply=True, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True,
                   judge_ask=ask_by_title({}, calls=calls))
    assert again["새 공고(중복 뺀)"] == 0 and calls == []                                       # 내일은 ×도 아는 공고 → 다시 묻지 않음


def test_judge_failure_keeps_everything(client):
    f = lambda url: LIST if "searchword=" in url else "<html></html>"
    def boom(p):
        raise RuntimeError("api down")
    out = co.run(["각"], apply=True, fetch=f, ref=TODAY, say=lambda *_: None, stop_known=99, max_career=-1, keep_grad=True, judge_ask=boom)
    assert out["저장한 새 공고"] == 4 and out["AI 제목 판정"]["실패한 묶음"] == 1 and out["AI 제목 판정"]["×"] == 0


def test_import_listing_wanted_from_pc(client):
    pages = [{"keyword": "로봇", "page": 1, "text": WANTED}, {"keyword": "ROS", "page": 1, "text": WANTED}]     # 같은 공고가 두 키워드에 겹침
    ask = ask_by_title({"책임": "×"}, default="○")
    pre = co.import_listing("wanted", pages, apply=False, ref=TODAY, exclude=[], max_career=-1, judge=False)
    assert pre["목록 건수"] == 6 and pre["새 공고"] == 3 and pre["중복"] == 3 and pre["mode"].startswith("미리보기")
    with SessionLocal() as db:
        assert db.execute(select(Job)).scalars().all() == []
    out = co.import_listing("wanted", pages, apply=True, ref=TODAY, exclude=[], max_career=-1, judge_ask=ask)
    assert out["저장한 새 공고"] == 2 and out["AI 제목 판정"]["×"] == 1
    with SessionLocal() as db:
        jobs = db.execute(select(Job)).scalars().all()
        assert {j.raw["출처"] for j in jobs} == {"원티드 수집"} and all(j.raw["관련도"] == "○" for j in jobs)
    again = co.import_listing("wanted", pages, apply=True, ref=TODAY, exclude=[], max_career=-1, judge_ask=ask_by_title({}))
    assert again["새 공고"] == 0 and again["이미 있음"] == 6                                      # 저장한 공고·×로 거른 공고 모두 아는 공고


def test_job_plan_and_import_endpoints(admin_client, client):
    r = admin_client.get("/api/admin/job-plan?pages=2")
    assert r.status_code == 200
    j = r.json()
    assert j["source"] == "wanted" and len(j["urls"]) == 2 * len(j["keywords"]) and "wanted.co.kr/api/chaos/search/v1/position" in j["urls"][0]["url"]
    assert admin_client.get("/api/admin/job-plan?source=saramin").status_code == 400
    body = {"source": "wanted", "apply": False, "pages": [{"keyword": "로봇", "page": 1, "text": WANTED}]}
    pre = admin_client.post("/api/admin/job-import", json=body).json()
    assert pre["목록 건수"] == 3 and pre["새 공고"] + sum(pre["조건으로 건너뜀"].values()) == 3 and pre["mode"].startswith("미리보기")   # 경력 조건에 걸린 건 빠짐
    assert admin_client.post("/api/admin/job-import", json={**body, "pages": []}).status_code == 422
    client.cookies.clear()
    assert client.get("/api/admin/job-plan").status_code in (401, 403)
    assert client.post("/api/admin/job-import", json=body).status_code in (401, 403)


def test_import_compact_items_and_known_in_plan(admin_client):
    items = [{"id": "9001", "t": "로봇 SW 개발자", "c": "A사", "f": 0, "to": 3, "e": "정규직"}, {"id": "9002", "t": "UX 디자이너", "c": "B사", "f": 1, "to": 3},
             {"id": "9001", "t": "로봇 SW 개발자", "c": "A사", "f": 0, "to": 3}]
    r = admin_client.post("/api/admin/job-import", json={"source": "wanted", "apply": False, "items": items}).json()
    assert r["받은 핵심 칸 줄"] == 3 and r["목록 건수"] == 3 and r["중복"] == 1 and r["새 공고"] == 2
    with SessionLocal() as db:
        co.save_drop(db, {"wanted:9002": "디자인"}, TODAY)
        db.commit()
    assert admin_client.get("/api/admin/job-plan").json()["known"] == ["9002"]            # ×로 거른 공고도 '아는 공고'
    out = co.import_listing("wanted", [], apply=True, ref=TODAY, exclude=[], max_career=-1, judge=False, compact=items[:1])
    assert out["저장한 새 공고"] == 1
    assert admin_client.get("/api/admin/job-plan").json()["known"] == ["9001", "9002"]
    assert admin_client.post("/api/admin/job-import", json={"source": "wanted"}).status_code == 422     # pages·items 둘 다 없음


def _p(title, company="A사", career="신입·경력", edu=""):
    return {"title": title, "company": company, "career": career, "edu": edu}


def test_career_cap_is_exactly_five_and_exclude_words_file():
    words = co.load_exclude()
    sk = lambda t, c="신입·경력": co.skip_reason(_p(t, career=c), co.MAX_CAREER, words, False)
    assert co.MAX_CAREER == 5
    assert sk("로봇 SW", "경력 5년↑") == "" and sk("로봇 SW", "경력 3~10년") == "" and sk("로봇 SW", "경력무관") == ""      # 5년까지(요구 최소 기준)는 수집
    assert sk("로봇 SW", "경력 6년↑") and sk("로봇 SW", "경력 6~10년")                                                    # 6년부터는 제외
    for t in ["로봇 사업 본부장", "서비스 기획자", "Product Manager", "기술영업 엔지니어", "생산직 오퍼레이터"]:      # 팀장·책임급은 상세를 보고 정함(test_senior_jev)
        assert sk(t), t
    for t in ["로봇 머신러닝 엔지니어 (Physical AI)", "온디바이스 AI/임베디드 ML 엔지니어", "임베디드 SW 개발자 (팀원)", "MES 생산관리 시스템 개발"]:
        assert sk(t) == "", t                                                                                              # 좋은 공고는 단어로 막지 않음(ML은 AI 판정이 맥락으로)
