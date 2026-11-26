"""하나의 원문 공고에 들어 있는 여러 직무를 검증된 직무별 공고로 저장한다.

입력은 원문 이미지/표를 읽고 사람이 확인한 JSON이다. 빈 자격요건을 다른
직무에서 가져오거나, 모호한 제목만으로 직무를 만들어 내지 않는다.

  python -m scripts.multi_role roles.json                 # 미리보기
  python -m scripts.multi_role roles.json --apply         # 검토 후 저장
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job
from scripts import sync_sheet as ss


# 내 구직 대상 직무. 일반 웹·앱과 제약 제조/영업 등은 자동 선택하지 않는다.
TARGET = re.compile(
    r"ROS|로봇\s*(?:SW|소프트웨어|제어|시스템)|임베디드|펌웨어|PLC|자동화\s*제어|"
    r"장비\s*(?:제어|SW|소프트웨어)|비전|영상\s*처리|컴퓨터\s*비전|QA|SW\s*테스트|"
    r"소프트웨어\s*검증|\bAI\b|LLM|RAG|인공지능|생성형|AX\s*엔지니어|MES|스마트\s*팩토리|자율주행",
    re.I,
)
EXCLUDE = re.compile(r"영업|마케팅|회계|인사|총무|구매|약사|약물|신약|항체|단백질|Scientist|생산직|포장|품질관리|기계\s*설계|전기\s*설계|웹\s*개발|프론트|백엔드", re.I)


def relevant(title: str) -> bool:
    """명확히 대상인 직무만 자동 선택. 애매한 직무는 검토 대상으로 남긴다."""
    return bool(TARGET.search(title or "")) and not bool(EXCLUDE.search(title or ""))


def role_key(parent: str, title: str) -> str:
    base = ss.canon(parent)
    digest = hashlib.sha256(re.sub(r"\s+", "", title).lower().encode()).hexdigest()[:16]
    return f"role:{base}:{digest}"


def _lines(value) -> str:
    if isinstance(value, list):
        return "\n".join("- " + str(x).strip() for x in value if str(x).strip())
    return str(value or "").strip()


def validate(payload: dict) -> tuple[str, list[dict], list[str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("roles"), list):
        raise ValueError("roles 배열이 필요합니다.")
    parent = str(payload.get("parent_source_key") or "").strip()
    if not parent:
        raise ValueError("parent_source_key가 필요합니다.")
    selected, excluded, seen = [], [], set()
    for i, role in enumerate(payload["roles"], 1):
        if not isinstance(role, dict):
            raise ValueError(f"{i}번 직무가 객체가 아닙니다.")
        title = re.sub(r"\s+", " ", str(role.get("title") or "")).strip()
        if not title:
            raise ValueError(f"{i}번 직무의 title이 비었습니다.")
        if title in seen:
            raise ValueError(f"같은 직무명이 중복되었습니다: {title}")
        seen.add(title)
        if not relevant(title):
            excluded.append(title)
            continue
        selected.append({**role, "title": title})
    return parent, selected, excluded


def import_roles(db, payload: dict, apply: bool = False) -> dict:
    parent_key, roles, excluded = validate(payload)
    parent = next((j for j in db.execute(select(Job)).scalars() if ss.canon(j.source_key) == ss.canon(parent_key)), None)
    if parent is None:
        raise ValueError(f"원본 공고가 DB에 없습니다: {parent_key}")
    existing = {j.source_key: j for j in db.execute(select(Job)).scalars()}
    planned = []
    for role in roles:
        title = role["title"]
        key = role_key(parent.source_key, title)
        old = existing.get(key)
        raw = dict((old.raw if old else parent.raw) or {})
        raw.pop("합본 공고 표시", None)
        raw.update({"공고명·담당 업무": title, "직무별 공고 키": key, "합본 공고 키": parent.source_key, "합본 공고명": parent.title,
                    "직무 분할 출처": "원문 확인 JSON", "필수·우대 출처": "직무별 원문 확인 JSON",
                    "필수": _lines(role.get("required")), "우대": _lines(role.get("preferred")),
                    "원문 본문": _lines(role.get("source_text"))})
        raw.pop("AI 읽은 날", None)
        if role.get("tasks"):
            raw["담당 업무"] = _lines(role["tasks"])
        if role.get("location"):
            raw["근무 위치"] = str(role["location"]).strip()[:100]
        if role.get("career"):
            raw["신입·경력"] = str(role["career"]).strip()
        if role.get("education"):
            raw["학력 요건"] = str(role["education"]).strip()
        raw.update(ss.classify(title, parent.company))
        # 원문 URL은 같은 링크를 유지하지만 source_key는 직무마다 독립적으로 만든다.
        planned.append((key, raw, old))
    if apply:
        for key, raw, old in planned:
            if old:
                old.title, old.location, old.raw = raw["공고명·담당 업무"], raw["근무 위치"], raw
            else:
                db.add(Job(source_key=key, company=parent.company, title=raw["공고명·담당 업무"],
                           location=raw["근무 위치"], status=parent.status, deadline_text=parent.deadline_text,
                           deadline_date=parent.deadline_date, deadline_time=parent.deadline_time,
                           last_checked=parent.last_checked, raw=raw))
        if planned:
            base = dict(parent.raw or {})
            base["직무별 공고 키"] = [key for key, _, _ in planned]
            base["합본 공고 표시"] = "직무별 공고로 분리됨"
            parent.raw = base
        db.commit()
    return {"mode": "저장함" if apply else "미리보기", "parent": parent.source_key,
            "selected": [{"title": raw["공고명·담당 업무"], "key": key, "required": bool(raw["필수"]),
                          "preferred": bool(raw["우대"]), "existing": bool(old)} for key, raw, old in planned],
            "excluded": excluded}


def main(argv=None):
    ap = argparse.ArgumentParser(description="확인된 복수 직무를 개별 공고로 저장")
    ap.add_argument("file", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    payload = json.loads(sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        print(json.dumps(import_roles(db, payload, args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
