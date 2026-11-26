from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config
from .db import init_db
from .routers import admin, ai, auth, docs, files, jobs, me

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 1단계에서는 단순하게 시작 시 테이블을 만듭니다. 스키마가 바뀌기 시작하면 Alembic을 도입하세요.
    init_db()
    yield


app = FastAPI(
    title="채용 보드 API",
    lifespan=lifespan,
    docs_url="/api/docs" if config.ENABLE_DOCS else None,
    openapi_url="/api/openapi.json" if config.ENABLE_DOCS else None,
    redoc_url=None,
)

_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def origin_check(request: Request, call_next):
    """브라우저가 다른 사이트에서 보낸 쓰기 요청(CSRF)을 막습니다.
    Origin 헤더가 있으면 같은 Host이거나 ALLOWED_ORIGINS에 있어야 합니다."""
    if request.method in _UNSAFE:
        origin = request.headers.get("origin")
        if origin:
            netloc = urlparse(origin).netloc
            host = request.headers.get("host", "")
            if netloc != host and origin not in config.ALLOWED_ORIGINS:
                return JSONResponse({"detail": "허용되지 않은 출처입니다."}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def no_store(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/public-config")
def public_config():
    """로그인 전 화면이 알아야 하는 공개 설정(비밀 아님)."""
    return {"demo": config.DEMO_ENABLED}


app.include_router(auth.router)
app.include_router(me.router)
app.include_router(jobs.router)
app.include_router(admin.router)
app.include_router(docs.router)
app.include_router(files.router)
app.include_router(ai.router)

if config.WEB_DIR:  # 개발용: 정적 파일도 같이 서빙(운영에서는 Caddy가 담당)
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
