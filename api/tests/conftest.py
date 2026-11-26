import os
import tempfile

# 앱을 불러오기 전에 테스트용 설정을 먼저 넣어야 합니다.
os.environ["DATABASE_URL"] = "sqlite://"  # 메모리 DB
os.environ["COOKIE_SECURE"] = "false"
os.environ["FILES_DIR"] = tempfile.mkdtemp(prefix="jb-files-")
os.environ["PRIVATE_DIR"] = tempfile.mkdtemp(prefix="jb-private-")

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app
from app.security import login_limiter
from scripts.create_admin import create
from scripts.import_seed import run as import_seed

ADMIN_EMAIL = "admin@example.com"
ADMIN_PW = "correct-horse-battery"


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    login_limiter.clear_all()
    with TestClient(app, base_url="http://testserver") as c:
        yield c


@pytest.fixture()
def seeded(client):
    return import_seed()


@pytest.fixture()
def admin_client(client):
    create(ADMIN_EMAIL, ADMIN_PW)
    r = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    assert r.status_code == 200
    return client


def new_client():
    return TestClient(app, base_url="http://testserver")
