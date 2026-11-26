"""관리자 계정을 만듭니다(비밀번호는 입력 프롬프트로 받아서 셸 기록에 남지 않습니다).

    python -m scripts.create_admin you@example.com
"""
import getpass
import sys

from sqlalchemy import select

from app import config
from app.db import SessionLocal, init_db
from app.models import User
from app.security import hash_password


def create(email: str, password: str) -> User:
    init_db()
    email = email.strip().lower()
    if len(password) < config.MIN_PASSWORD_LEN:
        raise SystemExit(f"비밀번호는 {config.MIN_PASSWORD_LEN}자 이상이어야 합니다.")
    with SessionLocal() as db:
        if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
            raise SystemExit("이미 있는 이메일입니다.")
        u = User(email=email, password_hash=hash_password(password), is_admin=True)
        db.add(u)
        db.commit()
        return u


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("사용법: python -m scripts.create_admin you@example.com")
    pw = getpass.getpass("비밀번호: ")
    if pw != getpass.getpass("비밀번호 확인: "):
        raise SystemExit("비밀번호가 서로 다릅니다.")
    u = create(sys.argv[1], pw)
    print(f"관리자 생성 완료: {u.email}")
