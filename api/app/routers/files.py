"""사용자 파일(제출 서류·자료실 원본) 업로드/다운로드.

- 확장자 화이트리스트 + 파일 머리글(시그니처) 검사. Content-Type은 사용자가 보낸 값이 아니라 서버가 정합니다.
- 파일은 서버 디스크(FILES_DIR/<소유자 id>/<파일 id>)에 저장하고, 접근은 소유자 또는 공용 자료실에 올린 파일만 허용합니다.
"""
import os
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from .. import config
from ..db import get_db
from ..deps import get_current_user, require_admin
from ..models import DemoUser, Document, StoredFile, User

router = APIRouter(tags=["files"])

# 확장자 → (Content-Type, 머리글 검사 함수)
_TYPES: dict[str, tuple[str, callable]] = {
    "pdf": ("application/pdf", lambda b: b.startswith(b"%PDF")),
    "png": ("image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    "jpg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    "jpeg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    "gif": ("image/gif", lambda b: b.startswith((b"GIF87a", b"GIF89a"))),
    "webp": ("image/webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
    "txt": ("text/plain; charset=utf-8", lambda b: b"\x00" not in b[:4096]),
    "md": ("text/plain; charset=utf-8", lambda b: b"\x00" not in b[:4096]),
}


def _err(status: int, code: str, msg: str) -> JSONResponse:
    return JSONResponse({"detail": msg, "code": code}, status_code=status)


def _path(owner_id: int, file_id: str) -> str:
    return os.path.join(config.FILES_DIR, str(owner_id), file_id)


@router.post("/api/files", status_code=201)
async def upload(file: UploadFile = File(...), user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    if db.get(DemoUser, user.id) is not None:
        return _err(403, "demo_no_upload", "데모 계정에서는 파일을 올릴 수 없어요.")
    name = os.path.basename((file.filename or "").replace("\\", "/")).strip()[:255] or "file"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in _TYPES:
        return _err(415, "unsupported_type", "지원하지 않는 파일 형식입니다(PDF·이미지·txt·md).")
    ctype, sniff = _TYPES[ext]

    used = db.execute(select(func.coalesce(func.sum(StoredFile.size), 0)).where(StoredFile.owner_id == user.id)).scalar_one()
    file_id = uuid.uuid4().hex
    dest = _path(user.id, file_id)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    size, head = 0, b""
    try:
        with open(dest, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                if not head:
                    head = chunk[:4096]
                size += len(chunk)
                if size > config.MAX_FILE_BYTES:
                    raise ValueError("too_large")
                if used + size > config.USER_QUOTA_BYTES:
                    raise ValueError("quota")
                out.write(chunk)
        if size == 0:
            raise ValueError("empty")
        if not sniff(head):
            raise ValueError("bad_signature")
    except ValueError as e:
        os.remove(dest) if os.path.exists(dest) else None
        code = str(e)
        msgs = {
            "too_large": f"파일이 너무 큽니다(최대 {config.MAX_FILE_BYTES // 1024 // 1024}MB).",
            "quota": "저장 공간을 다 썼습니다.",
            "empty": "빈 파일입니다.",
            "bad_signature": "파일 내용이 확장자와 맞지 않습니다.",
        }
        return _err(413 if code in ("too_large", "quota") else 400, code, msgs.get(code, "업로드하지 못했습니다."))

    db.add(StoredFile(id=file_id, owner_id=user.id, name=name, content_type=ctype, size=size))
    db.commit()
    return {"id": file_id, "name": name, "contentType": ctype.split(";")[0], "sizeBytes": size}


def _in_shared_library(db: DBSession, file_id: str) -> bool:
    """공용 자료실(library) 문서에 이 파일이 걸려 있으면 로그인한 모두가 볼 수 있습니다."""
    for row in db.execute(select(Document).where(Document.collection == "library")).scalars():
        files = (row.value or {}).get("files")
        if isinstance(files, list) and any(isinstance(f, dict) and f.get("id") == file_id for f in files):
            return True
    return False


@router.get("/_blob/{file_id}")
def download(file_id: str, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    rec = db.get(StoredFile, file_id) if file_id.isalnum() and len(file_id) == 32 else None
    if rec is None or not (rec.owner_id == user.id or _in_shared_library(db, rec.id)):
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    path = _path(rec.owner_id, rec.id)
    if not os.path.exists(path):
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    return FileResponse(
        path,
        media_type=rec.content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(rec.name)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/api/files/{file_id}", status_code=204)
def delete_file(file_id: str, user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    rec = db.get(StoredFile, file_id) if file_id.isalnum() and len(file_id) == 32 else None
    if rec is None or rec.owner_id != user.id:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    path = _path(rec.owner_id, rec.id)
    db.delete(rec)
    db.commit()
    if os.path.exists(path):
        os.remove(path)


# ---- 관리자(본인)에게만 내려가는 비공개 보드 코드 ----
@router.get("/api/private/board.js")
def private_board(admin: User = Depends(require_admin)):
    path = os.path.join(config.PRIVATE_DIR, "board_private.js")
    if not os.path.isfile(path):
        return JSONResponse({"detail": "비공개 파일이 없습니다."}, status_code=404)
    return FileResponse(path, media_type="application/javascript; charset=utf-8", headers={"Cache-Control": "private, no-store"})
