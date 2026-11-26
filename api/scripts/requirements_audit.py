"""DB 공고의 필수·우대 공란을 출처와 수집 상태별로 집계한다 (읽기 전용).

  python -m scripts.requirements_audit
  python -m scripts.requirements_audit --active-only
"""
import argparse
import json
from collections import Counter

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job
from app import gaps


def source_of(job: Job) -> str:
    key = job.source_key.lower()
    for source in ("saramin", "jobkorea", "wanted"):
        if source in key:
            return source
    return "sheet/other"


def reason(job: Job) -> str:
    raw = job.raw or {}
    if raw.get("이미지 공고"):
        if raw.get("AI 읽은 날"):
            return "이미지: AI 읽음, 해당 항목 추출 못함"
        return "이미지: AI 미실행/한도/실패"
    if source_of(job) == "jobkorea" and not raw.get("원문 본문"):
        return "잡코리아: 상세 본문 미수집"
    if raw.get("원문 본문"):
        return "본문 있음: 구역 제목 불일치/항목 미기재"
    return "본문 없음: 상세 미실행/실패/시트 입력"


def audit(jobs, active_only=False, company="") -> dict:
    selected = [j for j in jobs if not (j.raw or {}).get("합본 공고 표시") and (not active_only or gaps.is_active(j))
                and (not company or company.replace(" ", "") in j.company.replace(" ", ""))]
    blank = lambda j, k: gaps.is_blank((j.raw or {}).get(k))
    both = [j for j in selected if blank(j, "필수") and blank(j, "우대")]
    req = [j for j in selected if blank(j, "필수")]
    pref = [j for j in selected if blank(j, "우대")]
    return {
        "scope": "접수 상태 기준 마감 제외" if active_only else "전체(마감 포함)", "company": company or "전체", "total": len(selected),
        "required_blank": len(req), "preferred_blank": len(pref), "both_blank": len(both),
        "by_source": {s: {"total": sum(source_of(j) == s for j in selected),
                         "required_blank": sum(source_of(j) == s for j in req),
                         "preferred_blank": sum(source_of(j) == s for j in pref),
                         "both_blank": sum(source_of(j) == s for j in both)}
                      for s in sorted({source_of(j) for j in selected})},
        "required_blank_reasons": dict(Counter(reason(j) for j in req)),
        "preferred_blank_reasons": dict(Counter(reason(j) for j in pref)),
        "both_blank_reasons": dict(Counter(reason(j) for j in both)),
        "examples": [{"id": j.id, "company": j.company, "title": j.title, "source_key": j.source_key,
                      "reason": reason(j)} for j in both[:20]],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="필수·우대 공란 현황 (DB 읽기 전용)")
    ap.add_argument("--active-only", action="store_true")
    ap.add_argument("--company", default="", help="회사명에 들어간 글자 (예: 셀트리온제약)")
    args = ap.parse_args(argv)
    with SessionLocal() as db:
        print(json.dumps(audit(db.execute(select(Job)).scalars(), args.active_only, args.company), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
