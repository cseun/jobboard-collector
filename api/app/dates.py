"""공고 날짜(등록일·최초 수집) 처리. 날짜는 한국 시간(KST) 기준으로 셉니다.

- 등록일: 공고가 사이트에 올라온 날(사이트 표기 그대로 읽음)
- 최초 수집: 공고가 우리 보드에 처음 들어온 날
공고 원본(raw)에는 'YYYY-MM-DD' 글자로 넣고(보드가 그대로 읽음), DB 칸(posted_date·first_seen)에는 날짜로 복사해
정렬·조회에 씁니다.
"""
import re
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
RAW_POSTED = "등록일"
RAW_FIRST = "최초 수집"
RAW_FIRST_EST = "최초 수집 추정"      # True면 정확한 값이 아니라 '최종 확인' 날짜로 짐작해 넣은 값

_FULL = re.compile(r"^\s*(\d{4})[-./](\d{1,2})[-./](\d{1,2})")        # 2026-11-24, 2026.11.24
_YY = re.compile(r"^\s*(\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")    # 26/11/24 (사람인 등록일 표기)
_MD = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?!\d)")                     # 11/24 (보드의 '최종 확인' 표기)


def today_kst() -> date:
    return datetime.now(KST).date()


def parse_date(s, ref: date | None = None) -> date | None:
    """'2026-11-24'·'26/11/24'·'11/24' 같은 글자를 날짜로. 읽을 수 없으면 None.
    '11/24'처럼 연도가 없으면 기준일(ref) 연도로 보되, 기준일보다 이틀 넘게 미래면 작년으로 봅니다."""
    t = str(s or "")
    ref = ref or today_kst()
    try:
        m = _FULL.match(t)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _YY.match(t)
        if m:
            return date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _MD.match(t)
        if m:
            d = date(ref.year, int(m.group(1)), int(m.group(2)))
            return date(ref.year - 1, d.month, d.day) if (d - ref).days > 2 else d
    except ValueError:
        return None
    return None


def backfill_first_seen(raw: dict, ref: date) -> bool:
    """'최초 수집'이 없는 공고에 '최종 확인' 날짜로 짐작한 값을 넣고 추정 표시를 답니다. 바꿨으면 True."""
    if raw.get(RAW_FIRST):
        return False
    d = parse_date(raw.get("최종 확인"), ref)
    if d is None:
        return False
    raw[RAW_FIRST] = d.isoformat()
    raw[RAW_FIRST_EST] = True
    return True


def fill_job_dates(job) -> None:
    """공고 원본(raw)의 날짜 글자를 DB 칸(posted_date·first_seen)에 복사합니다."""
    raw = job.raw or {}
    job.posted_date = parse_date(raw.get(RAW_POSTED))
    job.first_seen = parse_date(raw.get(RAW_FIRST))
