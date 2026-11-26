"""이미 저장된 공고 중 관련 없는 것을 정리합니다. 기본은 미리보기(저장 안 함).

    docker compose exec -T api python -m scripts.cleanup_excluded                # 제외 단어(scripts/exclude_words.txt)에 걸린 공고 — 미리보기
    docker compose exec -T api python -m scripts.cleanup_excluded --x            # + AI가 ×로 판정한 공고(judge_existing·수집이 '관련도'에 적어 둔 것)
    docker compose exec -T api python -m scripts.cleanup_excluded --career 5     # + 요구 경력(최소)이 5년 초과인 공고
    docker compose exec -T api python -m scripts.cleanup_excluded --x --career 5 --apply   # 지우기
    docker compose exec -T api python -m scripts.cleanup_excluded --words 교사,튜터   # 단어를 직접 지정

안전장치
- 단어는 제목·회사 이름에서만 찾습니다(수집기와 같은 규칙). 시트에서 온 공고는 제목이 길어 본문 단어가 걸릴 수 있으니 목록을 꼭 확인하세요.
- 기본은 **수집기가 넣은 공고**('[신규·수집]')만 대상입니다. 시트에서 옮겨 온 공고까지 보려면 --all.
- 어떤 사용자의 개인 데이터(지원 현황·준비·메모 등)에 공고 주소/번호가 들어 있으면 **지우지 않습니다**.
- 지우기 전에 목록(회사·제목·걸린 단어)을 보여 줍니다. 지운 공고는 되돌릴 수 없으니 먼저 pg_dump 백업을 권합니다.
"""
import argparse
import json
import sys

from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.models import Document, Job, UserKV
from scripts import sync_sheet as ss
from scripts.collect import career_min, load_exclude

COLLECTED = "[신규·수집]"
SHOW = 80


def matched_word(job, words: list[str]) -> str:
    hay = f"{job.title or ''} {job.company or ''}".lower()
    return next((w for w in words if w.lower() in hay), "")


def reason(job, words: list[str], use_x: bool, max_career: int | None) -> str:
    w = matched_word(job, words)
    if w:
        return f"제외 단어 '{w}'"
    raw = job.raw or {}
    if use_x and raw.get("관련도") == "×":
        return "AI 판정 ×" + (f" ({raw['관련도 이유']})" if raw.get("관련도 이유") else "")
    if max_career is not None:
        c = career_min(str(raw.get("신입·경력") or raw.get("신입·경력(원문)") or ""))
        if c is not None and c > max_career:
            return f"경력 {c}년 이상 요구"
    return ""


def user_text(db) -> str:
    """개인 데이터 전체를 한 덩어리 글자로 — 공고 주소/번호가 들어 있는지 찾는 용도."""
    parts = [json.dumps(v, ensure_ascii=False) for v in db.execute(select(UserKV.value)).scalars()]
    parts += [json.dumps(d, ensure_ascii=False) for d in db.execute(select(Document.value)).scalars()]
    return "\n".join(parts)


def is_referenced(job, text: str) -> bool:
    keys = {job.source_key, ss.canon(job.source_key)}
    tail = ss.canon(job.source_key).split(":")[-1]
    return any(k and k in text for k in keys) or (len(tail) >= 6 and tail in text)


def run(apply: bool = False, words: list[str] | None = None, include_all: bool = False, use_x: bool = False, max_career: int | None = None, full: bool = False) -> dict:
    init_db()
    words = words if words is not None else load_exclude()
    hits, protected, other_origin = [], [], 0
    with SessionLocal() as db:
        text = user_text(db)
        for j in db.execute(select(Job)).scalars():
            w = reason(j, words, use_x, max_career)
            if not w:
                continue
            if not include_all and (j.raw or {}).get("구분") != COLLECTED:
                other_origin += 1
                continue
            (protected if is_referenced(j, text) else hits).append((j, w))
        by_word: dict[str, int] = {}
        for _, w in hits:
            k = w.split(" (")[0]
            by_word[k] = by_word.get(k, 0) + 1
        sample = [f"{w} | {j.company} | {j.title}" + (f" | {j.source_key}" if full else "") for j, w in (hits if full else hits[:SHOW])]
        if apply and hits:
            db.execute(delete(Job).where(Job.id.in_([j.id for j, _ in hits])))
            db.commit()
    return {"mode": "지움" if apply else "미리보기(저장 안 함)", "제외 단어": words, "지울 공고": len(hits), "이유별": by_word,
            "내 데이터에 쓰여서 남김": [f"{w} | {j.company} | {j.title}" for j, w in protected],
            "수집기 공고가 아니라 건너뜀(--all로 포함)": other_origin,
            ("목록(전체, 주소 포함)" if full else f"목록(최대 {SHOW}건)"): sample}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="제외 단어에 걸리는 저장된 공고 정리(기본: 미리보기)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--words", help="쉼표로 구분한 단어(없으면 exclude_words.txt)")
    ap.add_argument("--all", action="store_true", help="시트에서 옮겨 온 공고도 대상으로")
    ap.add_argument("--x", action="store_true", help="AI가 ×로 판정한 공고도 대상으로")
    ap.add_argument("--career", type=int, metavar="N", help="요구 경력(최소)이 N년을 넘는 공고도 대상으로")
    ap.add_argument("--full", action="store_true", help="지울 공고 전체를 주소와 함께 출력(구글 시트 정리용)")
    a = ap.parse_args(argv)
    words = [w.strip() for w in a.words.split(",") if w.strip()] if a.words else None
    print(json.dumps(run(a.apply, words, a.all, a.x, a.career, a.full), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
