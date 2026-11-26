import os
from datetime import date

from app.deadline import parse_deadline
from tests.conftest import ADMIN_EMAIL, ADMIN_PW, new_client

PW = "a-long-enough-pw"


def make_invite(admin, **kw):
    r = admin.post("/api/admin/invites", json=kw)
    assert r.status_code == 201, r.text
    return r.json()["code"]


def register(c, code, email="user1@example.com", pw=PW):
    return c.post("/api/auth/register", json={"email": email, "password": pw, "invite_code": code})


# ---------- 인증 ----------
def test_unauthenticated_is_rejected(client):
    for path in ["/api/me", "/api/jobs", "/api/board-data", "/api/me/data"]:
        assert client.get(path).status_code == 401, path


def test_register_requires_valid_invite(admin_client):
    other = new_client()
    assert register(other, "WRONG-CODE").status_code == 400


def test_invite_register_login_flow_and_cookie_flags(admin_client):
    code = make_invite(admin_client)
    other = new_client()
    r = register(other, code.lower())  # 소문자로 입력해도 통과
    assert r.status_code == 201
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    assert other.get("/api/me").json()["email"] == "user1@example.com"

    assert other.post("/api/auth/logout").status_code == 204
    assert other.get("/api/me").status_code == 401

    r = other.post("/api/auth/login", json={"email": "user1@example.com", "password": PW})
    assert r.status_code == 200 and r.json()["is_admin"] is False


def test_invite_is_single_use(admin_client):
    code = make_invite(admin_client)
    assert register(new_client(), code, "a@example.com").status_code == 201
    assert register(new_client(), code, "b@example.com").status_code == 400


def test_duplicate_email_and_weak_password(admin_client):
    c1, c2 = make_invite(admin_client), make_invite(admin_client)
    assert register(new_client(), c1, "dup@example.com").status_code == 201
    assert register(new_client(), c2, "dup@example.com").status_code == 409
    c3 = make_invite(admin_client)
    assert register(new_client(), c3, "weak@example.com", "short").status_code == 422
    # 실패한 가입에는 초대 코드가 소모되지 않아야 함
    assert register(new_client(), c3, "weak@example.com").status_code == 201


def test_login_wrong_password_and_rate_limit(admin_client):
    c = new_client()
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "nope-nope-nope"}).status_code == 401
    # 5회 실패 후에는 올바른 비밀번호도 잠시 막힘
    assert c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW}).status_code == 429


def test_unknown_email_gets_same_error(client):
    r = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "whatever-pw-1"})
    assert r.status_code == 401


def test_origin_check_blocks_cross_site_writes(admin_client):
    r = admin_client.post("/api/admin/invites", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = admin_client.post("/api/admin/invites", json={}, headers={"Origin": "http://testserver"})
    assert r.status_code == 201


# ---------- 관리자 ----------
def test_admin_endpoints_need_admin(admin_client):
    code = make_invite(admin_client)
    u = new_client()
    register(u, code)
    assert u.post("/api/admin/invites", json={}).status_code == 403
    assert u.get("/api/admin/invites").status_code == 403
    assert u.get("/api/admin/users").status_code == 403
    assert len(admin_client.get("/api/admin/users").json()) == 2


def test_used_invite_cannot_be_deleted(admin_client):
    code = make_invite(admin_client)
    register(new_client(), code)
    inv = [i for i in admin_client.get("/api/admin/invites").json() if i["code"] == code][0]
    assert inv["used"] is True
    assert admin_client.delete(f"/api/admin/invites/{inv['id']}").status_code == 409


# ---------- 공고 ----------
def test_seed_import_is_idempotent(seeded, admin_client):
    assert seeded["jobs_added"] == 510 and seeded["jobs_updated"] == 0
    from scripts.import_seed import run
    again = run()
    assert again["jobs_added"] == 0 and again["jobs_updated"] == 510


def test_jobs_list_search_and_detail(seeded, admin_client):
    r = admin_client.get("/api/jobs", params={"limit": 5}).json()
    assert r["total"] == 510 and len(r["items"]) == 5
    hit = admin_client.get("/api/jobs", params={"q": "로보틱박스"}).json()
    assert hit["total"] >= 1 and "로보틱박스" in hit["items"][0]["company"]
    detail = admin_client.get(f"/api/jobs/{hit['items'][0]['id']}").json()
    assert "raw" in detail and detail["raw"]["기업명"] == hit["items"][0]["company"]
    assert admin_client.get("/api/jobs/999999").status_code == 404


def test_board_data_has_same_shape_as_original(seeded, admin_client):
    d = admin_client.get("/api/board-data").json()
    assert len(d["jobs"]) == 510
    for key in ["companies", "jobplanet", "interviews", "overseas", "updated"]:
        assert key in d, key
    assert "원문 URL" in d["jobs"][0] and "요구" in d["jobs"][0]


import os as _os
import pytest as _pytest
_FORBID_FILE = _os.path.join(_os.path.dirname(__file__), "..", "..", "private", "forbidden.txt")
FORBIDDEN = [w.strip() for w in open(_FORBID_FILE, encoding="utf-8")] if _os.path.isfile(_FORBID_FILE) else []
FORBIDDEN = [w for w in FORBIDDEN if w]


def test_board_data_for_regular_user_has_no_personal_strings(seeded, admin_client):
    import json
    from tests.test_docs_files import two_users
    u1, _, _, _ = two_users(admin_client)
    blob = json.dumps(u1.get("/api/board-data").json(), ensure_ascii=False)
    if not FORBIDDEN:
        _pytest.skip("private/forbidden.txt 가 없어 금지 단어 검사를 건너뜁니다.")
    for w in FORBIDDEN:
        assert w not in blob, w


def test_tailored_docs_are_admin_only(client):
    """private/docs.json(본인 맞춤 자료)은 관리자만 받고, 일반 사용자에게는 빈 값."""
    import json
    from app import config
    from scripts.create_admin import create
    from scripts.import_seed import run
    from tests.test_docs_files import two_users

    with open(os.path.join(config.PRIVATE_DIR, "docs.json"), "w", encoding="utf-8") as f:
        json.dump({"(주)테스트": {"apply": {"flow": "서류→면접"}}}, f, ensure_ascii=False)
    try:
        run()
        create(ADMIN_EMAIL, ADMIN_PW)
        admin = new_client()
        assert admin.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW}).status_code == 200
        u1, _, _, _ = two_users(admin)
        assert "(주)테스트" in admin.get("/api/board-data").json()["docs"]
        assert u1.get("/api/board-data").json()["docs"] == {}
    finally:
        os.remove(os.path.join(config.PRIVATE_DIR, "docs.json"))


def test_import_without_private_docs_removes_stale_public_docs(client):
    """예전 버전이 공용 'docs' 키로 넣어 둔 값은 import_seed를 다시 돌리면 사라집니다."""
    from app.db import SessionLocal
    from app.models import SharedData
    from scripts.import_seed import run

    run()
    with SessionLocal() as db:
        db.add(SharedData(key="docs", value={"(주)누출": {}}))
        db.commit()
    run()
    with SessionLocal() as db:
        assert db.get(SharedData, "docs") is None


# ---------- 마감 파서 ----------
def test_deadline_parser():
    ref = date(2026, 11, 24)
    assert parse_deadline("11/27 23:59 (Unity 공고 · 11/18 공고는 마감)", ref) == (date(2026, 11, 27), "23:59")
    assert parse_deadline("11/25 24:00", ref) == (date(2026, 11, 25), "24:00")  # 시트에서 깨지던 형식
    assert parse_deadline("12/14(월)", ref) == (date(2026, 12, 14), None)
    assert parse_deadline("1/5 18:00", ref) == (date(2027, 1, 5), "18:00")
    assert parse_deadline("상시채용", ref) == (None, None)
    assert parse_deadline(None, ref) == (None, None)
    assert parse_deadline("2/30", ref)[0] is None


def test_seed_deadlines_are_split(seeded, admin_client):
    items = admin_client.get("/api/jobs", params={"limit": 200}).json()["items"]
    with_date = [i for i in items if i["deadline_date"]]
    assert len(with_date) > 100
    # 시트에서 깨지던 "24:00" 마감이 시간 문자열로 보존되는지 확인
    assert any(i["deadline_time"] == "24:00" for i in items)
    assert all(i["deadline_text"] for i in with_date)  # 원문도 그대로 남아 있음


# ---------- 개인 데이터 분리 ----------
def test_user_data_is_isolated(admin_client):
    u1, u2 = new_client(), new_client()
    assert register(u1, make_invite(admin_client), "u1@example.com").status_code == 201
    assert register(u2, make_invite(admin_client), "u2@example.com").status_code == 201

    assert u1.put("/api/me/data/profile", json={"value": {"name": "첫째", "n": [1, 2]}}).status_code == 200
    assert u1.get("/api/me/data/profile").json()["value"]["name"] == "첫째"

    assert u2.get("/api/me/data/profile").status_code == 404  # 다른 계정에는 안 보임
    u2.put("/api/me/data/profile", json={"value": {"name": "둘째"}})
    assert u1.get("/api/me/data/profile").json()["value"]["name"] == "첫째"  # 덮어쓰이지 않음
    assert [k["key"] for k in u1.get("/api/me/data").json()] == ["profile"]

    assert u1.delete("/api/me/data/profile").status_code == 204
    assert u1.get("/api/me/data/profile").status_code == 404
    assert u2.get("/api/me/data/profile").json()["value"]["name"] == "둘째"


def test_user_data_key_and_size_validation(admin_client):
    assert admin_client.put("/api/me/data/Bad Key!", json={"value": 1}).status_code in (400, 404)
    assert admin_client.put("/api/me/data/ok", json={"value": "x" * 1_100_000}).status_code == 413
    assert admin_client.put("/api/me/data/ok", json={"value": "x"}).status_code == 200
