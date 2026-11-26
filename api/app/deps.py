from datetime import timedelta

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from . import config
from .db import get_db
from .models import Session, User, utcnow
from .security import token_hash

COOKIE_NAME = "session"


def get_current_user(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: DBSession = Depends(get_db),
) -> User:
    if not session:
        raise HTTPException(401, "로그인이 필요합니다.")
    row = db.execute(
        select(Session).where(Session.token_hash == token_hash(session))
    ).scalar_one_or_none()
    if row is None or row.expires_at < utcnow():
        raise HTTPException(401, "로그인이 필요합니다.")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "관리자만 사용할 수 있습니다.")
    return user


def session_expiry():
    return utcnow() + timedelta(days=config.SESSION_DAYS)
