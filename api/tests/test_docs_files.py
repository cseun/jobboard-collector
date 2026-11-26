import os

from app import config
from tests.conftest import new_client
from tests.test_api import PW, make_invite, register

PDF = b"%PDF-1.4\n%test\n1 0 obj<<>>endobj\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def two_users(admin):
    u1, u2 = new_client(), new_client()
    assert register(u1, make_invite(admin), "u1@example.com").status_code == 201
    assert register(u2, make_invite(admin), "u2@example.com").status_code == 201
    return u1, u2, u1.get("/api/me").json()["id"], u2.get("/api/me").json()["id"]


def put(c, coll, id, data, **kw):
    return c.put("/api/docs/set", params={"c": coll, "id": id}, json={"data": data, **kw})


def get(c, coll, id):
    return c.get("/api/docs/get", params={"c": coll, "id": id})


# ---------- 문서 저장소 ----------
def test_docs_require_login(client):
    assert client.get("/api/docs/list", params={"c": "library"}).status_code == 401
    assert client.get("/_blob/" + "a" * 32).status_code == 401


def test_personal_collection_only_for_owner(admin_client):
    u1, u2, id1, id2 = two_users(admin_client)
    coll = f"data/users/{id1}"
    assert put(u1, coll, "profile", {"v": {"n": 1}, "at": 1}).status_code == 200
    r = get(u1, coll, "profile").json()
    assert r["exists"] and r["data"]["v"]["n"] == 1

    # 다른 사람의 개인 저장소: 읽기·쓰기·목록·삭제 모두 403
    assert get(u2, coll, "profile").status_code == 403
    assert put(u2, coll, "profile", {"v": {"n": 2}}).status_code == 403
    assert u2.get("/api/docs/list", params={"c": coll}).status_code == 403
    assert u2.delete("/api/docs/delete", params={"c": coll, "id": "profile"}).status_code == 403
    # 관리자도 남의 개인 저장소는 못 봄
    assert get(admin_client, coll, "profile").status_code == 403
    # 내 것은 그대로
    assert get(u1, coll, "profile").json()["data"]["v"]["n"] == 1


def test_get_missing_returns_exists_false_and_merge(admin_client):
    uid = admin_client.get("/api/me").json()["id"]
    coll = f"data/users/{uid}"
    assert get(admin_client, coll, "nope").json()["exists"] is False
    put(admin_client, coll, "d", {"a": 1, "b": 2})
    put(admin_client, coll, "d", {"b": 3, "c": 4}, merge=True)
    assert get(admin_client, coll, "d").json()["data"] == {"a": 1, "b": 3, "c": 4}
    put(admin_client, coll, "d", {"z": 1})  # merge 아님 → 통째로 교체
    assert get(admin_client, coll, "d").json()["data"] == {"z": 1}


def test_add_list_delete(admin_client):
    uid = admin_client.get("/api/me").json()["id"]
    coll = f"data/users/{uid}/notes"
    ids = [admin_client.post("/api/docs/add", params={"c": coll}, json={"data": {"t": i}}).json()["id"] for i in range(3)]
    assert len(set(ids)) == 3
    lst = admin_client.get("/api/docs/list", params={"c": coll}).json()["docs"]
    assert sorted(d["data"]["t"] for d in lst) == [0, 1, 2]
    assert admin_client.delete("/api/docs/delete", params={"c": coll, "id": ids[0]}).status_code == 204
    assert len(admin_client.get("/api/docs/list", params={"c": coll}).json()["docs"]) == 2


def test_shared_collection_read_all_write_admin_only(admin_client):
    u1, _, _, _ = two_users(admin_client)
    assert put(admin_client, "library", "x", {"title": "공용 자료"}).status_code == 200
    assert get(u1, "library", "x").json()["data"]["title"] == "공용 자료"
    assert [d["id"] for d in u1.get("/api/docs/list", params={"c": "library"}).json()["docs"]] == ["x"]
    assert put(u1, "library", "y", {"t": 1}).status_code == 403
    assert u1.post("/api/docs/add", params={"c": "library"}, json={"data": {}}).status_code == 403
    assert u1.delete("/api/docs/delete", params={"c": "library", "id": "x"}).status_code == 403


def test_feedback_visibility(admin_client):
    u1, u2, _, _ = two_users(admin_client)
    fid = u1.post("/api/docs/add", params={"c": "feedback"}, json={"data": {"text": "의견"}}).json()["id"]
    assert [d["id"] for d in u1.get("/api/docs/list", params={"c": "feedback"}).json()["docs"]] == [fid]
    assert u2.get("/api/docs/list", params={"c": "feedback"}).json()["docs"] == []  # 남의 의견은 안 보임
    assert get(u2, "feedback", fid).json()["exists"] is False
    assert put(u2, "feedback", fid, {"text": "덮어쓰기"}).status_code == 403
    assert len(admin_client.get("/api/docs/list", params={"c": "feedback"}).json()["docs"]) == 1  # 관리자는 봄


def test_pending_collection_rules(admin_client):
    u1, u2, id1, _ = two_users(admin_client)
    assert put(u1, f"pending/{id1}/jobs", "a", {"t": 1}).status_code == 200
    assert put(u2, f"pending/{id1}/jobs", "b", {"t": 1}).status_code == 403
    assert get(u2, f"pending/{id1}/jobs", "a").status_code == 403
    assert get(admin_client, f"pending/{id1}/jobs", "a").json()["exists"]  # 관리자는 읽기 가능
    assert put(admin_client, f"pending/{id1}/jobs", "c", {}).status_code == 403  # 쓰기는 불가


def test_docs_validation(admin_client):
    uid = admin_client.get("/api/me").json()["id"]
    bad = [("../x", "a"), ("data//x", "a"), ("a/./b", "a"), ("", "a"), ("x" * 201, "a"), ("a b", "a")]
    for c, i in bad:
        assert admin_client.get("/api/docs/get", params={"c": c, "id": i}).status_code in (400, 422, 404), c
    assert get(admin_client, f"data/users/{uid}", "bad id!").status_code == 400
    assert put(admin_client, f"data/users/{uid}", "big", {"x": "y" * 1_100_000}).status_code == 413
    assert admin_client.put("/api/docs/set", params={"c": f"data/users/{uid}", "id": "a"}, json={"data": [1, 2]}).status_code == 422


# ---------- 파일 ----------
def upload(c, name, data, ctype="application/octet-stream"):
    return c.post("/api/files", files={"file": (name, data, ctype)})


def test_upload_download_delete(admin_client):
    u1, u2, _, _ = two_users(admin_client)
    r = upload(u1, "이력서_테스트.pdf", PDF)
    assert r.status_code == 201, r.text
    info = r.json()
    assert info["contentType"] == "application/pdf" and info["sizeBytes"] == len(PDF)

    d = u1.get(f"/_blob/{info['id']}")
    assert d.status_code == 200 and d.content == PDF
    assert d.headers["content-type"] == "application/pdf"
    assert d.headers["x-content-type-options"] == "nosniff"
    assert "filename*=UTF-8''" in d.headers["content-disposition"]

    # 다른 사람은 내 파일을 못 받음(존재 여부도 숨김)
    assert u2.get(f"/_blob/{info['id']}").status_code == 404
    assert admin_client.get(f"/_blob/{info['id']}").status_code == 404
    assert u2.delete(f"/api/files/{info['id']}").status_code == 404

    assert u1.delete(f"/api/files/{info['id']}").status_code == 204
    assert u1.get(f"/_blob/{info['id']}").status_code == 404
    assert not os.path.exists(os.path.join(config.FILES_DIR, str(u1.get('/api/me').json()['id']), info["id"]))


def test_upload_rejects_bad_files(admin_client):
    c = admin_client
    r = upload(c, "evil.html", b"<script>alert(1)</script>")
    assert r.status_code == 415 and r.json()["code"] == "unsupported_type"
    assert upload(c, "evil.svg", b"<svg onload=alert(1)>").status_code == 415
    assert upload(c, "fake.pdf", b"<html>not a pdf</html>").status_code == 400  # 확장자와 내용 불일치
    assert upload(c, "empty.pdf", b"").status_code == 400
    assert upload(c, "x.png", PNG).status_code == 201
    # 파일 이름의 경로 조작은 이름만 남김
    r = upload(c, "../../etc/passwd.txt", b"hello")
    assert r.status_code == 201 and r.json()["name"] == "passwd.txt"


def test_upload_size_limit(admin_client, monkeypatch):
    monkeypatch.setattr(config, "MAX_FILE_BYTES", 1024)
    r = upload(admin_client, "big.pdf", PDF + b"x" * 2048)
    assert r.status_code == 413 and r.json()["code"] == "too_large"


def test_upload_quota(admin_client, monkeypatch):
    monkeypatch.setattr(config, "USER_QUOTA_BYTES", len(PDF) * 2 + 10)
    assert upload(admin_client, "a.pdf", PDF).status_code == 201
    assert upload(admin_client, "b.pdf", PDF).status_code == 201
    r = upload(admin_client, "c.pdf", PDF)
    assert r.status_code == 413 and r.json()["code"] == "quota"


def test_library_file_visible_to_everyone(admin_client):
    u1, _, _, _ = two_users(admin_client)
    fid = upload(admin_client, "자료.pdf", PDF).json()["id"]
    assert u1.get(f"/_blob/{fid}").status_code == 404  # 아직 비공개
    put(admin_client, "library", "doc1", {"title": "공용", "files": [{"id": fid, "name": "자료.pdf"}]})
    assert u1.get(f"/_blob/{fid}").status_code == 200  # 공용 자료실에 걸리면 모두에게 열림
    assert u1.delete(f"/api/files/{fid}").status_code == 404  # 지우는 건 주인만


# ---------- 비공개 보드 코드 ----------
def test_private_board_is_admin_only(admin_client):
    u1, _, _, _ = two_users(admin_client)
    assert admin_client.get("/api/private/board.js").status_code == 404  # 파일 없음
    with open(os.path.join(config.PRIVATE_DIR, "board_private.js"), "w", encoding="utf-8") as f:
        f.write("window.__PRIVATE__={ok:1};")
    r = admin_client.get("/api/private/board.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"] and "__PRIVATE__" in r.text
    assert u1.get("/api/private/board.js").status_code == 403
    assert new_client().get("/api/private/board.js").status_code == 401
