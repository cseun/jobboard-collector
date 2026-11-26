import hashlib
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import config

_ph = PasswordHasher()
# 존재하지 않는 이메일로 로그인할 때도 같은 시간이 걸리도록 쓰는 더미 해시
_DUMMY_HASH = _ph.hash("dummy-password-for-timing")

_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 0/O/1/I 제외


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(stored_hash: str | None, pw: str) -> bool:
    try:
        return _ph.verify(stored_hash or _DUMMY_HASH, pw) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _ph.check_needs_rehash(stored_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_invite_code() -> str:
    raw = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_invite(code: str) -> str:
    return code.strip().upper()


class LoginLimiter:
    """(IP, 이메일)별 실패 횟수 제한. 프로세스 메모리 기반이라 워커 1개일 때 정확합니다."""

    def __init__(self):
        self._fails: dict[str, deque] = defaultdict(deque)

    def _prune(self, key: str, now: float):
        q = self._fails[key]
        while q and now - q[0] > config.LOGIN_WINDOW_SEC:
            q.popleft()
        if not q:
            self._fails.pop(key, None)

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        if key in self._fails:
            self._prune(key, now)
        return len(self._fails.get(key, ())) >= config.LOGIN_MAX_FAILS

    def fail(self, key: str):
        self._fails[key].append(time.monotonic())

    def reset(self, key: str):
        self._fails.pop(key, None)

    def clear_all(self):
        self._fails.clear()


login_limiter = LoginLimiter()
