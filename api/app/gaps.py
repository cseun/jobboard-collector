"""공고의 '빈칸' 찾기와 채우기. 서버가 못 채운 칸을 내 PC(또는 사람)가 API로 받아 가서 채워 넣을 때 씁니다.

- 빈칸: 값이 없거나 '원문 확인 필요'·'미확인' 같은 자리표시 글자인 칸. (우대의 '—'는 '없음'이라는 뜻이라 빈칸이 아님)
- 채우기: 기본은 빈칸만 채움(이미 값이 있으면 건너뜀). overwrite=True 일 때만 덮어씀. 채운 곳·날짜는 '채움 기록'에 남김.
"""
from datetime import date

from .dates import RAW_POSTED, fill_job_dates, parse_date, today_kst

PLACEHOLDERS = {"", "원문 확인 필요", "미확인", "확인 필요", "-", "n/a", "N/A"}
GAP_FIELDS = ("필수", "우대", "급여(공고)", "전형 절차", "근무 위치", RAW_POSTED)
WRITABLE = GAP_FIELDS + ("상세 주소", "복지", "신입·경력", "학력 요건")
MAX_LEN = 4000
LOCATION_FIELD = "근무 위치"


def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, dict)):
        return not v
    return str(v).strip() in PLACEHOLDERS


def blanks_of(raw: dict, fields=GAP_FIELDS) -> list[str]:
    return [f for f in fields if is_blank((raw or {}).get(f))]


def is_active(job) -> bool:
    return not str(job.status or "").startswith("마감")


def gap_item(job, fields=GAP_FIELDS) -> dict | None:
    raw = job.raw or {}
    b = blanks_of(raw, fields)
    if not b:
        return None
    return {"id": job.id, "company": job.company, "title": job.title, "url": job.source_key, "status": job.status, "blanks": b,
            "원문 본문": (raw.get("원문 본문") or "")[:MAX_LEN], "이미지 공고": bool(raw.get("이미지 공고")), "이미지 주소": raw.get("이미지 주소") or [],
            "AI 읽은 날": raw.get("AI 읽은 날"), "현재 값": {f: raw.get(f) for f in fields if f not in b and raw.get(f) not in (None, "")}}


def apply_fill(job, fields: dict, by: str = "pc", overwrite: bool = False, today: date | None = None) -> dict:
    """허용된 칸만, 기본은 빈칸만 채웁니다. {'applied': [...], 'skipped': {칸: 이유}}"""
    today = today or today_kst()
    raw = dict(job.raw or {})
    applied, skipped = [], {}
    log = dict(raw.get("채움 기록") or {})
    for k, v in (fields or {}).items():
        if k not in WRITABLE:
            skipped[k] = "채울 수 없는 칸"
            continue
        if isinstance(v, list):
            v = "\n".join(f"- {x}" for x in v if str(x).strip())
        v = str(v if v is not None else "").strip()
        if not v or v in PLACEHOLDERS:
            skipped[k] = "빈 값"
            continue
        if len(v) > MAX_LEN:
            skipped[k] = f"너무 김({MAX_LEN}자 초과)"
            continue
        if not overwrite and not is_blank(raw.get(k)):
            skipped[k] = "이미 값이 있음"
            continue
        if k == RAW_POSTED:
            d = parse_date(v, today)
            if d is None or d > today:
                skipped[k] = "읽을 수 없거나 미래인 날짜"
                continue
            v = d.isoformat()
        raw[k] = v
        log[k] = f"{by} {today.isoformat()}"
        applied.append(k)
        if k == LOCATION_FIELD:
            job.location = v
    if applied:
        raw["채움 기록"] = log
        job.raw = raw
        fill_job_dates(job)
    return {"applied": applied, "skipped": skipped}
