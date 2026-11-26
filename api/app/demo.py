"""가입 없이 둘러보는 임시 데모 계정의 정리."""
import os
import shutil
import time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from . import config
from .models import AiUsage, DemoUser, Document, Session, StoredFile, User, UserAiKey, UserApiKey, UserKV, utcnow

_last_run = 0.0


def delete_user_everything(db: DBSession, uid: int) -> None:
    for model, col in ((Session, Session.user_id), (UserKV, UserKV.user_id), (Document, Document.owner_id),
                       (StoredFile, StoredFile.owner_id), (UserApiKey, UserApiKey.user_id), (UserAiKey, UserAiKey.user_id),
                       (AiUsage, AiUsage.user_id), (DemoUser, DemoUser.user_id)):
        db.execute(delete(model).where(col == uid))
    db.execute(delete(User).where(User.id == uid))
    shutil.rmtree(os.path.join(config.FILES_DIR, str(uid)), ignore_errors=True)


def cleanup_expired(db: DBSession, throttle_sec: int = 600, force: bool = False) -> int:
    """만료된 데모 계정과 그 데이터를 지웁니다. 데모 시작 요청 때 가끔(기본 10분에 한 번) 같이 실행됩니다."""
    global _last_run
    now = time.monotonic()
    if not force and now - _last_run < throttle_sec:
        return 0
    _last_run = now
    ids = db.execute(select(DemoUser.user_id).where(DemoUser.expires_at < utcnow())).scalars().all()
    for uid in ids:
        delete_user_everything(db, uid)
    db.commit()
    return len(ids)
