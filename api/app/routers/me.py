import json
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .. import config
from ..db import get_db
from ..deps import get_current_user
from ..models import DemoUser, User, UserKV, utcnow
from ..schemas import KVIn

router = APIRouter(prefix="/api/me", tags=["me"])
_KEY = re.compile(r"^[a-z0-9_.:-]{1,64}$")


def _check_key(key: str) -> str:
    if not _KEY.match(key):
        raise HTTPException(400, "키 형식이 올바르지 않습니다(영문 소문자·숫자·_.:- 64자 이내).")
    return key


@router.get("")
def me(user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    return {"id": user.id, "email": user.email, "is_admin": user.is_admin, "is_demo": db.get(DemoUser, user.id) is not None}


@router.get("/data")
def list_keys(user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    rows = db.execute(select(UserKV).where(UserKV.user_id == user.id).order_by(UserKV.key)).scalars().all()
    return [{"key": r.key, "updated_at": r.updated_at} for r in rows]


# 개인 데이터는 항상 user.id(로그인한 사람)로만 조회합니다. 요청에서 user_id를 받지 않습니다.
@router.get("/data/{key}")
def get_value(key: str, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    row = db.get(UserKV, (user.id, _check_key(key)))
    if row is None:
        raise HTTPException(404, "저장된 값이 없습니다.")
    return {"key": row.key, "value": row.value, "updated_at": row.updated_at}


@router.put("/data/{key}")
def put_value(key: str, body: KVIn, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    key = _check_key(key)
    size = len(json.dumps(body.value, ensure_ascii=False).encode())
    if size > config.MAX_KV_BYTES:
        raise HTTPException(413, f"값이 너무 큽니다({size} bytes, 최대 {config.MAX_KV_BYTES}).")
    row = db.get(UserKV, (user.id, key))
    if row is None:
        row = UserKV(user_id=user.id, key=key, value=body.value)
        db.add(row)
    else:
        row.value = body.value
        row.updated_at = utcnow()
    db.commit()
    return {"key": key, "updated_at": row.updated_at}


@router.delete("/data/{key}", status_code=204)
def delete_value(key: str, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    row = db.get(UserKV, (user.id, _check_key(key)))
    if row is not None:
        db.delete(row)
        db.commit()
