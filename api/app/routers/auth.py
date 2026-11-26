import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from .. import config
from ..db import get_db
from ..deps import COOKIE_NAME, session_expiry
from ..demo import cleanup_expired
from ..models import DemoUser, InviteCode, Session, User, utcnow
from ..schemas import LoginIn, RegisterIn
from ..security import (
    hash_password,
    login_limiter,
    needs_rehash,
    new_session_token,
    normalize_invite,
    token_hash,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _start_session(db: DBSession, user: User, response: Response, expires_at=None) -> None:
    token = new_session_token()
    expires_at = expires_at or session_expiry()
    db.add(Session(token_hash=token_hash(token), user_id=user.id, expires_at=expires_at))
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max(60, int((expires_at - utcnow()).total_seconds())),
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


@router.post("/register", status_code=201)
def register(body: RegisterIn, response: Response, db: DBSession = Depends(get_db)):
    email = body.email.lower()
    code = normalize_invite(body.invite_code)
    now = utcnow()

    invite = db.execute(select(InviteCode).where(InviteCode.code == code)).scalar_one_or_none()
    if invite is None or invite.used_by is not None or (invite.expires_at and invite.expires_at < now):
        raise HTTPException(400, "초대 코드가 올바르지 않거나 이미 사용됐습니다.")
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(409, "이미 가입된 이메일입니다.")

    user = User(email=email, password_hash=hash_password(body.password), is_admin=False)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "이미 가입된 이메일입니다.")

    # 동시에 같은 코드로 가입해도 한 명만 통과하도록 조건부 UPDATE로 한 번만 사용 처리
    res = db.execute(
        update(InviteCode)
        .where(InviteCode.id == invite.id, InviteCode.used_by.is_(None))
        .values(used_by=user.id, used_at=now)
    )
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "초대 코드가 올바르지 않거나 이미 사용됐습니다.")
    db.commit()

    _start_session(db, user, response)
    return {"id": user.id, "email": user.email}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DBSession = Depends(get_db)):
    email = body.email.lower()
    ip = request.client.host if request.client else "?"
    limiter_key = f"{ip}|{email}"
    if login_limiter.blocked(limiter_key):
        raise HTTPException(429, "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.")

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not verify_password(user.password_hash if user else None, body.password):
        login_limiter.fail(limiter_key)
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")

    login_limiter.reset(limiter_key)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        db.commit()
    _start_session(db, user, response)
    return {"id": user.id, "email": user.email, "is_admin": user.is_admin}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DBSession = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        row = db.execute(select(Session).where(Session.token_hash == token_hash(token))).scalar_one_or_none()
        if row:
            db.delete(row)
            db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


_demo_hits: dict[str, deque] = defaultdict(deque)


@router.post("/demo", status_code=201)
def start_demo(request: Request, response: Response, db: DBSession = Depends(get_db)):
    """가입 없이 둘러보는 임시 계정을 만들고 로그인시킵니다(DEMO_ENABLED=true일 때만).
    계정마다 따로 만들어지므로 방문자끼리 데이터가 섞이지 않고, 시간이 지나면 데이터와 함께 지워집니다."""
    if not config.DEMO_ENABLED:
        raise HTTPException(404, "데모 모드가 꺼져 있습니다.")
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    q = _demo_hits[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= config.DEMO_PER_HOUR_PER_IP:
        raise HTTPException(429, "데모를 너무 자주 시작했어요. 잠시 뒤 다시 시도해 주세요.")
    q.append(now)

    cleanup_expired(db)
    expires = utcnow() + timedelta(hours=config.DEMO_HOURS)
    user = User(email=f"demo-{secrets.token_hex(6)}@demo.invalid", password_hash=hash_password(secrets.token_urlsafe(32)), is_admin=False)
    db.add(user)
    db.flush()
    db.add(DemoUser(user_id=user.id, expires_at=expires))
    db.commit()
    _start_session(db, user, response, expires_at=expires)
    return {"id": user.id, "demo": True, "expires_at": expires}
