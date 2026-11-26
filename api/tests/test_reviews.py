from datetime import date

from sqlalchemy import select

from app import reviews
from app.db import SessionLocal
from app.models import Document, SharedData, User
from tests.conftest import new_client

TODAY = date(2026, 11, 25)
ROW = {"src": "캐치", "url": "https://www.catch.co.kr/Comp/CompMajor/1/interview", "role": "SW개발 신입(2025)", "flow": "서류 → 1차 실무 → 2차 임원",
       "qs": "자기소개, 지원동기, 프로젝트 꼬리질문", "tips": "프로젝트 담당 파트를 숫자로 말하기", "n": 3}
EX = {"t": "SW개발 신입", "y": "2025 하반기", "spec": "지방대 · 학점 4.0", "pts": ["지원동기를 회사 한 줄 정의로 시작"], "url": "https://jasoseol.com/x/1", "src": "자소설닷컴", "login": "로그인 필요"}


def test_merge_interviews_new_row_append_and_dedupe():
    rows, n = reviews.merge_interviews([], "(주)가나", [ROW], TODAY)
    assert n == 1 and rows[0]["기업"] == "(주)가나" and "[캐치 2026-11-25 수집]" in rows[0]["전형 흐름"] and rows[0]["후기"].startswith("후기 3건")
    again, n2 = reviews.merge_interviews(rows, "가나", [ROW], TODAY)          # 같은 주소는 다시 반영하지 않음(회사 이름이 조금 달라도 같은 회사)
    assert n2 == 0 and again == rows
    other = {**ROW, "src": "잡플래닛", "url": "https://www.jobplanet.co.kr/x", "qs": "인성 질문 위주", "n": 1}
    rows3, n3 = reviews.merge_interviews(rows, "가나", [other], date(2026, 12, 1))
    assert n3 == 1 and len(rows3) == 1 and rows3[0]["전형 흐름"].startswith("**[캐치 2026-11-25") and "인성 질문 위주" in rows3[0]["나온 질문"]   # 기존 글은 앞에 그대로
    assert rows3[0]["출처"].count("](") == 2 and rows3[0]["후기"].endswith("+ 추가 1건") and rows3[0]["확인일"] == "2026-12-01"


def test_merge_examples_dedupe_and_keep_existing():
    docs, n = reviews.merge_examples({"가나": {"q": [{"q": "x", "limit": 1}], "ex": [{"t": "내가 넣은 것", "url": "https://mine"}]}}, "(주)가나", [EX], TODAY)
    assert n == 1 and docs["가나"]["q"] and [x["t"] for x in docs["가나"]["ex"]] == ["내가 넣은 것", "SW개발 신입"]
    assert reviews.merge_examples(docs, "가나", [EX], TODAY)[1] == 0


def test_import_endpoint_admin_only_and_targets(admin_client):
    r = admin_client.post("/api/admin/review-import", json={"company": "(주)가나", "rows": [ROW], "examples": [EX]})
    assert r.status_code == 200 and r.json()["후기 반영"] == 1 and r.json()["예시 반영"] == 1
    r = admin_client.post("/api/admin/review-import", json={"company": "(주)가나", "rows": [ROW], "examples": [EX]})
    assert r.json()["후기 반영"] == 0 and r.json()["건너뜀(이미 있는 주소)"] == 2
    with SessionLocal() as db:
        assert db.get(SharedData, "interviews").value[0]["기업"] == "(주)가나" and db.get(SharedData, "admin:docs").value["(주)가나"]["ex"][0]["src"] == "자소설닷컴"
    bad = admin_client.post("/api/admin/review-import", json={"company": "x", "rows": [{**ROW, "url": "javascript:alert(1)"}]})
    assert bad.status_code == 422
    assert admin_client.post("/api/admin/review-import", json={"company": "x", "rows": [{**ROW, "qs": "가" * 3000}]}).status_code == 422     # 길이 제한
    anon = new_client()
    assert anon.get("/api/admin/review-targets").status_code in (401, 403) and anon.post("/api/admin/review-import", json={"company": "x"}).status_code in (401, 403)


def test_targets_from_prep_and_apps_skip_recent_and_failed(admin_client):
    with SessionLocal() as db:
        uid = db.execute(select(User)).scalars().first().id
        db.add(Document(collection="prep", doc_id="sr1", value={"company": "(주)준비회사", "status": "요청됨"}))
        db.add(Document(collection=f"data/users/{uid}", doc_id="apps", value={"v": [{"기업": "지원회사", "결과": "면접"}, {"기업": "탈락회사", "결과": "불합격"}, {"기업": "(주)준비회사", "결과": ""}, {"기업": "최근수집", "결과": ""}]}))
        db.add(SharedData(key="reviewImports", value={reviews.norm("최근수집"): "2026-11-01"}))
        db.commit()
    t = admin_client.get("/api/admin/review-targets?limit=10").json()["targets"]
    assert [x["company"] for x in t] == ["(주)준비회사", "지원회사"] and t[0]["why"] == "지원 준비 요청"      # 탈락·최근 수집·중복은 제외
    admin_client.post("/api/admin/review-import", json={"company": "지원회사", "rows": []})                  # 아무것도 못 찾아도 날짜를 남겨 60일 동안 다시 안 읽음
    assert [x["company"] for x in admin_client.get("/api/admin/review-targets?limit=10").json()["targets"]] == ["(주)준비회사"]
