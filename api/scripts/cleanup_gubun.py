"""시트로 들어온 옛 공고의 '[신규·시트]' 표시를 '[이월]'로 바꿉니다(1회성). 기본은 미리보기(저장 안 함).
신규 표시는 이제 날짜(등록일·최초 수집)로 판단하므로 옛 표시가 남아 있으면 헷갈립니다.

    docker compose exec -T api python -m scripts.cleanup_gubun            # 미리보기
    docker compose exec -T api python -m scripts.cleanup_gubun --apply    # 저장

- '[신규·시트]'가 정확히 같은 구분만 바꿉니다. '[신규·수집]'(수집기가 넣은 것)과 다른 구분은 건드리지 않습니다.
- 여러 번 실행해도 같습니다.
"""
import argparse
import json
import sys

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Job

OLD, NEW = "[신규·시트]", "[이월]"


def run(apply: bool = False) -> dict:
    init_db()
    n = 0
    with SessionLocal() as db:
        for j in db.execute(select(Job)).scalars():
            if (j.raw or {}).get("구분") == OLD:
                n += 1
                if apply:
                    raw = dict(j.raw)
                    raw["구분"] = NEW
                    j.raw = raw
        if apply:
            db.commit()
    return {"mode": "저장함" if apply else "미리보기(저장 안 함)", f"'{OLD}' → '{NEW}'": n}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="'[신규·시트]' 표시 정리(기본: 미리보기)")
    ap.add_argument("--apply", action="store_true")
    print(json.dumps(run(ap.parse_args(argv).apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
