"""API 키 암호화(Fernet = AES-128-CBC + HMAC-SHA256, 변조 검출 포함).

- 마스터 키는 환경변수(APP_ENCRYPTION_KEY)에만 있고 DB에는 없습니다. DB만 유출돼도 키를 읽을 수 없습니다.
- 평문에 사용자 id를 같이 넣어 암호화하므로, 다른 사람 행의 암호문을 복사해 붙여도 복호화 단계에서 거부됩니다.
"""
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from . import config


class CryptoError(Exception):
    pass


def _fernet() -> MultiFernet | None:
    keys = [k.strip() for k in config.APP_ENCRYPTION_KEY.split(",") if k.strip()]
    if not keys:
        return None
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except (ValueError, TypeError):
        return None


def enabled() -> bool:
    return _fernet() is not None


def encrypt(user_id: int, secret: str) -> str:
    f = _fernet()
    if f is None:
        raise CryptoError("APP_ENCRYPTION_KEY가 설정되지 않았거나 형식이 올바르지 않습니다.")
    return f.encrypt(f"{user_id}\n{secret}".encode()).decode()


def decrypt(user_id: int, token: str) -> str:
    f = _fernet()
    if f is None:
        raise CryptoError("APP_ENCRYPTION_KEY가 설정되지 않았습니다.")
    try:
        plain = f.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        raise CryptoError("저장된 키를 복호화하지 못했습니다(마스터 키가 바뀌었을 수 있어요).")
    uid, _, secret = plain.partition("\n")
    if uid != str(user_id):
        raise CryptoError("저장된 키의 소유자가 맞지 않습니다.")
    return secret
