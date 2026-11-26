"""문서 저장소 API. 보드의 저장 호출(컬렉션/문서)을 서버로 옮기기 위한 통로입니다.

접근 규칙(컬렉션 경로 기준)
- data/users/<내 id>/...   : 본인만 읽고 쓰기
- pending/<내 id>/...      : 본인 읽기·쓰기, 관리자는 읽기
- feedback                 : 로그인한 누구나 쓰기, 읽기는 본인 문서만(관리자는 전부)
- 그 밖의 공용 컬렉션      : 로그인한 누구나 읽기, 쓰기는 관리자만
"""
import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from .. import config
from ..db import get_db
from ..deps import get_current_user
from ..models import DemoUser, Document, User, utcnow

router = APIRouter(prefix="/api/docs", tags=["docs"])

_SEG = r"[A-Za-z0-9_.:\-]+"
_COLL = re.compile(rf"^{_SEG}(/{_SEG})*$")
_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


class DocIn(BaseModel):
    data: dict[str, Any]
    merge: bool = False


def _coll(c: str) -> str:
    c = c.strip("/")
    if not c or len(c) > 200 or not _COLL.match(c) or any(s in ("", ".", "..") for s in c.split("/")):
        raise HTTPException(400, "컬렉션 경로가 올바르지 않습니다.")
    return c


def _id(i: str) -> str:
    if not _ID.match(i) or i in (".", ".."):
        raise HTTPException(400, "문서 id가 올바르지 않습니다.")
    return i


def _check_access(user: User, coll: str, write: bool) -> None:
    segs = coll.split("/")
    ok: bool
    if segs[0] == "data" and len(segs) >= 3 and segs[1] == "users":
        ok = segs[2] == str(user.id)
    elif segs[0] == "pending":
        ok = (len(segs) >= 2 and segs[1] == str(user.id)) or (not write and user.is_admin)
    elif segs[0] == "feedback":
        ok = True
    else:
        ok = (not write) or user.is_admin
    if not ok:
        raise HTTPException(403, "접근할 수 없는 저장소입니다.")


def _visible(user: User, coll: str, row: Document) -> bool:
    """feedback은 본인 문서(또는 관리자)만 보입니다."""
    if coll.split("/")[0] != "feedback":
        return True
    return user.is_admin or row.owner_id == user.id


def _check_size(data: dict) -> None:
    size = len(json.dumps(data, ensure_ascii=False).encode())
    if size > config.MAX_KV_BYTES:
        raise HTTPException(413, f"문서가 너무 큽니다({size} bytes, 최대 {config.MAX_KV_BYTES}).")


def _view(row: Document) -> dict:
    return {"id": row.doc_id, "data": row.value, "updated_at": row.updated_at}


def _check_count(db: DBSession, user: User) -> None:
    n = db.execute(select(func.count()).select_from(Document).where(Document.owner_id == user.id)).scalar_one()
    cap = config.DEMO_MAX_DOCS if db.get(DemoUser, user.id) is not None else config.MAX_DOCS_PER_USER
    if n >= cap:
        raise HTTPException(413, "저장할 수 있는 문서 수를 넘었습니다.")


@router.get("/list")
def list_docs(c: str = Query(...), user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    c = _coll(c)
    _check_access(user, c, write=False)
    rows = db.execute(select(Document).where(Document.collection == c).order_by(Document.doc_id)).scalars().all()
    return {"docs": [_view(r) for r in rows if _visible(user, c, r)]}


@router.get("/get")
def get_doc(c: str = Query(...), id: str = Query(...), user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    c, id = _coll(c), _id(id)
    _check_access(user, c, write=False)
    row = db.get(Document, (c, id))
    if row is None or not _visible(user, c, row):
        return {"exists": False, "id": id, "data": None}
    return {"exists": True, **_view(row)}


@router.put("/set")
def set_doc(
    body: DocIn,
    c: str = Query(...),
    id: str = Query(...),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    c, id = _coll(c), _id(id)
    _check_access(user, c, write=True)
    row = db.get(Document, (c, id))
    if row is not None and c.split("/")[0] == "feedback" and not user.is_admin and row.owner_id != user.id:
        raise HTTPException(403, "접근할 수 없는 문서입니다.")
    data = {**row.value, **body.data} if (row is not None and body.merge) else body.data
    _check_size(data)
    if row is None:
        _check_count(db, user)
        row = Document(collection=c, doc_id=id, value=data, owner_id=user.id)
        db.add(row)
    else:
        row.value = data
        row.updated_at = utcnow()
    db.commit()
    return {"id": id, "updated_at": row.updated_at}


@router.post("/add", status_code=201)
def add_doc(
    body: DocIn,
    c: str = Query(...),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    c = _coll(c)
    _check_access(user, c, write=True)
    _check_size(body.data)
    _check_count(db, user)
    new_id = uuid.uuid4().hex[:20]
    db.add(Document(collection=c, doc_id=new_id, value=body.data, owner_id=user.id))
    db.commit()
    return {"id": new_id}


@router.delete("/delete", status_code=204)
def delete_doc(c: str = Query(...), id: str = Query(...), user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    c, id = _coll(c), _id(id)
    _check_access(user, c, write=True)
    row = db.get(Document, (c, id))
    if row is None:
        return
    if c.split("/")[0] == "feedback" and not user.is_admin and row.owner_id != user.id:
        raise HTTPException(403, "접근할 수 없는 문서입니다.")
    db.delete(row)
    db.commit()
