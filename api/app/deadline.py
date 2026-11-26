"""'11/27 23:59 (Unity 공고 ...)', '12/1 24:00', '상시' 같은 마감 문구를 날짜와 시간으로 분리.

시트에서 '11/26 24:00'이 시간 값으로 바뀌어 깨졌던 문제를 막기 위해,
날짜(Date)와 시간(문자열 'HH:MM')을 따로 저장합니다. 원문은 deadline_text에 그대로 남깁니다.
"""
import re
from datetime import date

_PAT = re.compile(r"(\d{1,2})/(\d{1,2})(?:\([^)]*\))?(?:\s+(\d{1,2}):(\d{2}))?")


def parse_deadline(text: str | None, ref: date) -> tuple[date | None, str | None]:
    if not text:
        return None, None
    m = _PAT.search(text)
    if not m:
        return None, None
    month, day = int(m.group(1)), int(m.group(2))
    time_s = None
    if m.group(3) is not None:
        hh, mm = int(m.group(3)), int(m.group(4))
        if (hh, mm) == (24, 0) or (0 <= hh <= 23 and 0 <= mm <= 59):
            time_s = f"{hh:02d}:{mm:02d}"
    # 기준일보다 한참 이른 월이면 다음 해 공고로 봅니다(예: 11월 기준 1/5 마감).
    year = ref.year + 1 if month < ref.month - 1 else ref.year
    try:
        return date(year, month, day), time_s
    except ValueError:
        return None, time_s
