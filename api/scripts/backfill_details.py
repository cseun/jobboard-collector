"""기존 접수 중 공고의 원문을 다시 읽어, 확인된 빈 상세 칸만 채운다.

python -m scripts.backfill_details             # 대상 미리보기
python -m scripts.backfill_details --apply --max 20
"""
import argparse
import json
import sys
from collections import Counter
from datetime import date

import httpx
from sqlalchemy import select

from app import gaps
from app.dates import today_kst
from app.db import SessionLocal, init_db
from app.models import CollectRun, Job, utcnow
from scripts import collect as co
from scripts import sync_sheet as ss
from scripts.collect_sources import SOURCES, jobkorea_parse_detail, jobkorea_parse_frame
from scripts.text_requirements import parse_sections

MAX_TRIES = 2
FIELDS = ("담당 업무", "필수", "우대", "복지", "전형 절차")


def targets(db, ref: date, limit: int) -> list[Job]:
    out = []
    for j in db.scalars(select(Job).order_by(Job.id.desc())):
        raw = j.raw or {}
        kind = ss.canon(j.source_key).split(":", 1)[0]
        if kind not in ("saramin", "jobkorea") or not gaps.is_active(j) or (j.deadline_date and j.deadline_date < ref):
            continue
        if raw.get("관련도") == "×" or raw.get("상세 재조회 시도", 0) >= MAX_TRIES or raw.get("상세 재조회일") == ref.isoformat():
            continue
        if not any(gaps.is_blank(raw.get(k)) for k in FIELDS) and raw.get("원문 본문"):
            continue
        out.append(j)
        if len(out) >= limit:
            break
    return out


def extract(j: Job, fetch) -> dict:
    kind, rid = ss.canon(j.source_key).split(":", 1)
    raw = j.raw or {}
    saved = str(raw.get("원문 본문") or "").strip()
    if len(saved) >= 80:
        return {"text": saved, "images": raw.get("이미지 주소") or [], "is_image": bool(raw.get("이미지 공고"))}
    url = SOURCES[kind].view(rid)
    if kind == "saramin":
        return co.parse_detail(fetch(SOURCES[kind].detail_url(rid)))
    detail = jobkorea_parse_detail(fetch(url))
    if not detail["text"] and detail["iframe"]:
        from urllib.parse import urljoin
        detail = {**detail, **jobkorea_parse_frame(fetch(urljoin(url, detail["iframe"])))}
    return detail


def fill(j: Job, info: dict, ref: date) -> str:
    raw = dict(j.raw or {})
    raw["상세 재조회 시도"] = int(raw.get("상세 재조회 시도") or 0) + 1
    raw["상세 재조회일"] = ref.isoformat()
    if (info.get("company_fields") and info.get("company_name")
            and co.company_name_key(info["company_name"]) == co.company_name_key(j.company)):
        fields = info["company_fields"]
        if gaps.is_blank(raw.get("회사 정보")):
            raw["회사 정보"] = fields
            raw["회사 정보 출처"] = j.source_key
        if gaps.is_blank(raw.get("회사 설명·제품")) and fields.get("업종"):
            raw["회사 설명·제품"] = fields["업종"]
        if gaps.is_blank(raw.get("규모·사원수")) and fields.get("사원수") not in (None, "", "-"):
            raw["규모·사원수"] = fields["사원수"]
    images = info.get("images") or []
    if images:
        raw["이미지 주소"] = images[:6]
        raw["이미지 공고"] = bool(info.get("is_image"))
    text = (info.get("text") or "").strip()
    if info.get("is_image"):
        raw["상세 재조회 결과"] = "이미지 OCR 검증 대기"
    elif not text:
        raw["상세 재조회 결과"] = "본문 별도 로딩 또는 읽기 실패"
    else:
        if gaps.is_blank(raw.get("원문 본문")):
            raw["원문 본문"] = text[:co.BODY_KEEP]
        sections = parse_sections(text)
        for key in FIELDS:
            if sections.get(key) and gaps.is_blank(raw.get(key)):
                raw[key] = sections[key]
        if sections.get("담당 업무") and sections.get("필수"):
            raw["상세 확인일"] = ref.isoformat()
            raw["상세 재조회 결과"] = "업무·필수 확인"
        else:
            raw["상세 재조회 결과"] = "업무·필수 구분 실패"
    j.raw = raw
    return raw["상세 재조회 결과"]


def run(apply=False, max_n=20, fetch=None, ref=None) -> dict:
    ref = ref or today_kst()
    init_db()
    fetch = fetch or co.Fetcher(max_requests=max_n * 2 + 5).get
    result = {"대상": 0, "결과": {}, "오류": []}
    with SessionLocal() as db:
        todo = targets(db, ref, max_n)
        result["대상"] = len(todo)
        result["예시"] = [f"{j.company} — {j.title}" for j in todo[:5]]
        if not apply:
            return result
        rec = CollectRun(day=ref.isoformat(), mode="기존 공고 상세 재조회", status="실행 중", options={"max": max_n})
        db.add(rec)
        db.commit()
        counts = Counter()
        requests = 0
        for j in todo:
            try:
                info = extract(j, fetch)
                requests += 1
                outcome = fill(j, info, ref)
            except co.Blocked as exc:
                rec.status = "차단으로 중단"
                rec.blocked = str(exc)[:200]
                result["중단"] = rec.blocked
                break
            except (httpx.HTTPError, ValueError) as exc:
                result["오류"].append(f"{j.id}: {type(exc).__name__}"[:100])
                outcome = "요청 오류"
                raw = dict(j.raw or {})
                raw["상세 재조회 시도"] = int(raw.get("상세 재조회 시도") or 0) + 1
                raw["상세 재조회일"] = ref.isoformat()
                raw["상세 재조회 결과"] = outcome
                j.raw = raw
            counts[outcome] += 1
            db.commit()
        rec.finished_at = utcnow()
        if rec.status == "실행 중":
            rec.status = "완료"
        rec.requests = rec.detail_requests = requests
        rec.updated = counts["업무·필수 확인"]
        rec.notes = result["오류"][:20]
        result["결과"] = dict(counts)
        db.commit()
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="기존 접수 중 공고의 상세 본문·업무·필수 재조회")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--max", type=int, default=20)
    args = p.parse_args(argv)
    if args.max < 1 or args.max > 200:
        p.error("--max는 1~200")
    result = run(apply=args.apply, max_n=args.max)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("중단") else 0


if __name__ == "__main__":
    sys.exit(main())
