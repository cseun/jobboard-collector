"""만료된 데모 계정 정리(자동으로도 실행되지만, 수동으로 돌릴 때):  python -m scripts.cleanup_demo"""
from app.db import SessionLocal
from app.demo import cleanup_expired

if __name__ == "__main__":
    with SessionLocal() as db:
        print(f"지운 데모 계정: {cleanup_expired(db, force=True)}개")
