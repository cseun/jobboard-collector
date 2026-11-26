"""구글 시트(CSV)의 공고를 DB에 반영합니다. AI를 쓰지 않아 토큰이 들지 않습니다.

기본은 '미리보기'(저장 안 함)이고, --apply 를 붙여야 저장됩니다.

    python -m scripts.sync_sheet 시트.csv                 # 미리보기
    python -m scripts.sync_sheet 시트.csv --apply         # 저장
    python -m scripts.sync_sheet - --apply < 시트.csv      # 파일 대신 표준 입력(도커 안에서 쓰기 편함)
    python -m scripts.sync_sheet --url "https://docs.google.com/spreadsheets/d/<ID>/export?format=csv&gid=<탭ID>" --apply

주소는 환경변수 SHEET_CSV_URL 로 미리 넣어 둘 수도 있습니다(--url 이 우선).
컨테이너 안에서는: docker compose exec api python -m scripts.sync_sheet /srv/files/시트.csv

읽을 수 있는 시트 모양 3가지(머리글 줄을 보고 자동으로 알아봅니다):
  ① 기업명 + 직무명                     (지원 검토용 '미지원'·'자동정렬' 탭)
  ② 기업명 + 공고명·담당 업무           (내 수집 시트의 '채용공고' 탭 — 보드와 같은 열 이름)
  ③ 기업명 + 공고명                     (내 수집 시트의 '추가수집_…' 탭들)
탭이 여러 개면 탭마다 CSV로 받아 한 번씩 실행하세요(여러 번 해도 중복이 생기지 않습니다).

가져오는 것: 기업명 · 직무명 · 근무지 · 경력/학력 조건 · 마감 · 채용 사이트와 주소 · 원문 접수 상태 · 확인일 · 등록일(시트에 '등록일' 열이 있을 때).
새로 들어오는 공고에는 '최초 수집'(보드가 처음 받은 날, 한국 시간 오늘)을 기록합니다.
가져오지 않는 것: 지원 상태 · 적합도 · 적합 이유 · 확인 내용(메모). 시트를 만든 사람의 개인 판단이라 내 보드에 섞지 않습니다.

이미 보드에 있는 공고(같은 사람인·잡코리아·원티드 번호)는 '접수 상태 · 마감 · 최종 확인'만 새 값으로 바꾸고,
비어 있는 칸만 채웁니다. 이미 채워진 설명·필수·우대 등은 지우거나 덮어쓰지 않습니다.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.dates import RAW_FIRST, RAW_POSTED, fill_job_dates, parse_date, today_kst
from app.deadline import parse_deadline
from app.models import Job

MAX_BYTES = 8 * 1024 * 1024
_URL = re.compile(r"https?://[^\s|)\]\"']+")
_ISO = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})")

# 시트 열 이름 → 내부 이름 (같은 이름이 두 번 나오면 첫 번째 열만 씁니다)
WANT = {
    "기업명": "company", "직무명": "title", "기간 구분": "period", "마감일": "due", "마감 상태": "due_state",
    "경력 조건": "career", "학력 조건": "edu", "근무지": "loc", "채용 사이트": "site", "상세 URL": "url",
    "확인일": "checked", "원문 접수 상태": "src_status", "접수 시작일": "start", "등록일": "posted",
}

# ② 내 수집 시트 '채용공고' 탭(보드와 같은 열 이름). 접수 상태·마감은 시트 글자 그대로 씁니다.
WANT_B = {
    "기업명": "company", "공고명·담당 업무": "title", "원문 URL": "url", "사이트": "site", "근무 위치": "loc",
    "접수 상태": "src_status", "마감(KST)": "due", "신입·경력(원문)": "career", "학력 요건(원문)": "edu", "최종 확인": "checked",
    "등록일": "posted",
}
# ③ '추가수집_…' 탭: 마감 칸에 날짜 또는 '상시'·'채용시'·'원문 확인'이 들어 있습니다.
WANT_C = {
    "기업명": "company", "공고명": "title", "원문 URL": "url", "사이트": "site", "근무 위치": "loc",
    "경력 요건": "career", "학력 요건": "edu", "마감": "due", "수집일": "checked", "등록일": "posted",
}

ROLE_RULES = [  # (직무 분류 이름, 정규식) — 위에서부터 처음 맞는 것 하나
    ("SW 테스트·검증(QA)", r"QA|테스트|검증|V&V|HILS|SILS"),
    ("임베디드·펌웨어", r"펌웨어|임베디드|firmware|embedded|MCU|STM32|RTOS"),
    ("로봇 SW(제어·ROS)", r"ROS|로봇\s*(SW|소프트웨어|제어|시스템)|robot\s*(software|control)|모션|motion"),
    ("AI·LLM·AX", r"\bAI\b|LLM|AX|머신러닝|딥러닝|인공지능|생성형|에이전트|agent"),
    ("비전·영상처리 SW", r"비전|영상|vision|OpenCV"),
    ("PLC·자동화 제어", r"PLC|자동화\s*제어|시퀀스"),
    ("MES·스마트팩토리 SW", r"MES|스마트\s*팩토리|스마트팩토리"),
    ("자율주행", r"자율주행|autonomous"),
    ("장비 제어 SW(PC제어)", r"장비\s*(제어|SW|소프트웨어)|설비"),
    ("웹·앱·서버", r"웹|앱|서버|백엔드|프론트|풀스택|backend|frontend"),
]
FIELD_RULES = [  # (분야 이름, 정규식) — 맞는 것을 모두 담음. 못 찾으면 비워 둠(보드에서 '미분류')
    ("🤖 로봇", r"로봇|robot|로보|AMR|협동"),
    ("🔬 반도체·디스플레이 장비", r"반도체|디스플레이|웨이퍼"),
    ("👁 머신비전·검사장비", r"비전|검사|vision"),
    ("🚗 자동차·자율주행", r"자동차|자율주행|모빌리티"),
    ("🏭 공장자동화·스마트팩토리", r"스마트\s*팩토리|공장\s*자동화|PLC|MES|자동화"),
    ("💻 AI·SW 솔루션", r"\bAI\b|LLM|인공지능|솔루션"),
    ("🧪 SW 테스트·검증", r"QA|테스트|검증"),
    ("🔋 2차전지 장비", r"2차\s*전지|이차전지|배터리"),
    ("🛡 방산·항공", r"방산|항공|우주|국방"),
    ("🏥 의료기기", r"의료|메디"),
]


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def read_source(src: str) -> str:
    """파일 경로 또는 https 주소에서 CSV 글을 읽습니다."""
    if re.match(r"^https?://", src):
        if urlparse(src).scheme != "https":
            raise ValueError("주소는 https 만 쓸 수 있어요.")
        req = urllib.request.Request(src, headers={"User-Agent": "jobboard-sheet-sync/1"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (https만 허용)
            data = r.read(MAX_BYTES + 1)
    elif src == "-":  # 표준 입력: docker compose exec -T api python -m scripts.sync_sheet - < 시트.csv
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
    else:
        with open(src, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("파일이 너무 커요(8MB 초과).")
    text = data.decode("utf-8-sig", errors="replace")
    if text.lstrip().lower().startswith("<!doctype html") or "<html" in text[:300].lower():
        raise ValueError("CSV가 아니라 웹 페이지가 왔어요. 시트 공유 설정(링크가 있는 모든 사용자 보기)이나 주소를 확인하세요.")
    return text


OPTIONAL = {"posted"}      # 시트에 없어도 '없는 열'로 알리지 않는 열


def parse_rows(text: str) -> tuple[list[dict], list[str]]:
    """제목 줄이 몇 줄 있어도 머리글 줄(기업명 + 직무명/공고명…)을 찾아 그 아래를 읽습니다."""
    rows = list(csv.reader(io.StringIO(text)))
    fmt, hi = None, None
    for i, r in enumerate(rows):
        cells = [c.strip() for c in r]
        if "기업명" not in cells:
            continue
        if "직무명" in cells:
            fmt, want = "A", WANT
        elif "공고명·담당 업무" in cells:
            fmt, want = "B", WANT_B
        elif "공고명" in cells:
            fmt, want = "C", WANT_C
        else:
            continue
        hi = i
        break
    if hi is None:
        raise ValueError("머리글 줄(기업명 + 직무명/공고명)을 찾지 못했어요. 채용 공고 탭을 CSV로 받았는지, 다른 시트(예: Jira)가 아닌지 확인하세요.")
    idx: dict[str, int] = {}
    for i, h in enumerate(rows[hi]):
        key = want.get(h.strip())
        if key and key not in idx:
            idx[key] = i
    out = []
    for r in rows[hi + 1:]:
        d = {k: _clean(r[i]) if i < len(r) else "" for k, i in idx.items()}
        # 줄바꿈이 들어간 셀은 한 줄로 합쳤습니다. URL은 첫 주소만 씁니다.
        m = _URL.search(d.get("url", ""))
        d["url"] = m.group(0).rstrip(".,") if m else ""
        if fmt == "B":
            d["exact_status"] = True            # 시트의 접수 상태·마감 글자를 그대로 씀
        elif fmt == "C":
            due = d.get("due", "")
            if due and not _ISO.match(due):     # 날짜가 아니면 상태 글자로 취급('상시' 등)
                d["due_state"] = due
        out.append(d)
    return out, [h for h, k in want.items() if k not in idx and k not in OPTIONAL]   # 등록일 열은 없어도 정상


def canon(url_or_key: str) -> str:
    """같은 공고를 다른 주소 모양으로 적어 둔 것을 하나로 봅니다(사람인·잡코리아·원티드는 공고 번호로)."""
    u = (url_or_key or "").strip()
    for name, pat in (("saramin", r"rec_idx=(\d+)"), ("jobkorea", r"GI_Read/(\d+)"), ("wanted", r"/wd/(\d+)")):
        m = re.search(pat, u)
        if m:
            return f"{name}:{m.group(1)}"
    return re.sub(r"[#?].*$", "", u).rstrip("/").lower() if u.startswith("http") else u.lower()


def _co(s: str) -> str:
    return re.sub(r"\(주\)|㈜|주식회사|\(유\)|[\s·()（）\-_]", "", s or "").lower()


def _md(s: str) -> str:
    m = _ISO.match(s or "")
    return f"{int(m.group(2))}/{int(m.group(3))}" if m else (s or "")


def _status(d: dict) -> str:
    if d.get("exact_status") and d.get("src_status"):
        return d["src_status"]
    src, state = d.get("src_status", ""), d.get("due_state", "")
    if re.search(r"마감|종료|closed", src, re.I) or re.search(r"지원\s*마감|마감됨|종료", state):
        return "마감"
    base = "접수중" if (not src or re.search(r"모집|접수|진행|open", src, re.I)) else src
    if "상시" in state:
        return "상시(채용시 마감)"
    if "채용시" in state or ("채용" in state and "마감" in state):
        return "접수중(채용시 마감)"
    return base


def _due(d: dict) -> str:
    due = d.get("due", "")
    return _md(due) if _ISO.match(due) else (due or d.get("due_state", ""))


def classify(title: str, company: str) -> dict:
    roles = [name for name, pat in ROLE_RULES if re.search(pat, title, re.I)][:2]
    text = f"{company} {title}"
    fields = [name for name, pat in FIELD_RULES if re.search(pat, text, re.I)][:3]
    out = {"직무 분류": roles or ["기타 SW"]}
    if fields:
        out["분야"] = fields
    return out


def to_raw(d: dict, ref: date | None = None) -> dict | None:
    """시트 한 줄 → 보드 공고 한 건(시드와 같은 모양). 회사·직무 이름이 없으면 건너뜁니다."""
    if not d.get("company") or not d.get("title"):
        return None
    site = _clean((d.get("site") or "").split("/")[0]) or "원문"
    raw = {
        "구분": d.get("gubun", "[신규·시트]"), "기업명": d["company"], "근무 위치": d.get("loc") or "미확인", "규모·사원수": "", "회사 설명·제품": "",
        "공고명·담당 업무": d["title"], "원문 URL": f"[{site}]({d['url']})" if d.get("url") else "", "접수 상태": _status(d),
        "마감(KST)": _due(d), "신입·경력": d.get("career") or "", "학력 요건": d.get("edu") or "", "필수": "", "우대": "",
        "최종 확인": _md(d.get("checked", "")), "출처": d.get("origin", "시트"),
    }
    if d.get("csn"):
        raw["csn"] = d["csn"]
    raw.update(classify(d["title"], d["company"]))
    ref = ref or today_kst()
    raw[RAW_FIRST] = ref.isoformat()                         # 보드가 처음 받은 날
    posted = parse_date(d.get("posted"), ref)
    if posted:
        raw[RAW_POSTED] = posted.isoformat()                 # 시트에 등록일 열이 있을 때만
    return raw


FRESH = ("접수 상태", "마감(KST)", "최종 확인")                     # 새 값으로 바꾸는 칸
FILL = ("근무 위치", "신입·경력", "학력 요건", "원문 URL", RAW_POSTED, "csn")   # 비어 있을 때만 채우는 칸(최초 수집은 바꾸지 않음)
EMPTY = {"", "미확인", "-", None}


def plan(rows: list[dict], existing: list[Job], ref: date) -> dict:
    by_key = {canon(j.source_key): j for j in existing}
    by_name = {(_co(j.company), _co(j.title)) for j in existing}
    job_by_name = {(_co(j.company), _co(j.title)): j for j in existing}
    new, upd, same, dups, skipped, seen = [], [], 0, [], 0, set()
    for d in rows:
        raw = to_raw(d, ref)
        if raw is None:
            skipped += 1
            continue
        key = d.get("url") or f"sheet:{raw['기업명']}:{raw['공고명·담당 업무']}"
        ck = canon(key)
        if ck in seen:  # 시트 안의 중복 줄
            skipped += 1
            continue
        seen.add(ck)
        job = by_key.get(ck)
        if job is None and not d.get("url"):   # 주소가 없는 줄은 회사·공고명으로 같은 공고를 찾음
            job = job_by_name.get((_co(raw["기업명"]), _co(raw["공고명·담당 업무"])))
        if job is None:
            if (_co(raw["기업명"]), _co(raw["공고명·담당 업무"])) in by_name:
                dups.append(f"{raw['기업명']} — {raw['공고명·담당 업무']}")
                continue
            new.append((key, raw))
            continue
        cur, changes = dict(job.raw or {}), {}
        for k in FRESH:
            if raw.get(k) and raw[k] != cur.get(k):
                changes[k] = raw[k]
        for k in FILL:
            if raw.get(k) not in EMPTY and cur.get(k) in EMPTY:
                changes[k] = raw[k]
        if changes:
            upd.append((job, changes))
        else:
            same += 1
    return {"new": new, "upd": upd, "same": same, "dups": dups, "skipped": skipped, "ref": ref}


def apply_plan(db, p: dict) -> None:
    ref = p["ref"]
    for key, raw in p["new"]:
        dd, dt = parse_deadline(raw.get("마감(KST)"), ref)
        job = Job(source_key=key, company=raw["기업명"], title=raw["공고명·담당 업무"], location=raw["근무 위치"], status=raw["접수 상태"],
                  deadline_text=raw["마감(KST)"], deadline_date=dd, deadline_time=dt, last_checked=raw["최종 확인"], raw=raw)
        fill_job_dates(job)
        db.add(job)
    for job, ch in p["upd"]:
        raw = dict(job.raw or {})
        raw.update(ch)
        job.raw = raw  # 새 dict를 넣어야 변경이 저장됩니다
        if "접수 상태" in ch:
            job.status = ch["접수 상태"]
        if "마감(KST)" in ch:
            job.deadline_text = ch["마감(KST)"]
            job.deadline_date, job.deadline_time = parse_deadline(ch["마감(KST)"], ref)
        if "최종 확인" in ch:
            job.last_checked = ch["최종 확인"]
        if "근무 위치" in ch:
            job.location = ch["근무 위치"]
        fill_job_dates(job)


def run(src: str, apply: bool = False, today: date | None = None) -> dict:
    ref = today or today_kst()      # 서버가 UTC여도 한국 날짜로 셈
    rows, missing = parse_rows(read_source(src))
    init_db()
    with SessionLocal() as db:
        p = plan(rows, list(db.execute(select(Job)).scalars()), ref)
        if apply:
            apply_plan(db, p)
            db.commit()
    return {
        "mode": "저장함" if apply else "미리보기(저장 안 함)", "시트 줄": len(rows), "새로 추가": len(p["new"]), "최신 값으로 갱신": len(p["upd"]),
        "변화 없음": p["same"], "이름이 같아 건너뜀(확인 필요)": len(p["dups"]), "건너뜀(빈 줄·중복)": p["skipped"],
        "없는 열": missing, "새 공고 예시": [f"{r['기업명']} — {r['공고명·담당 업무']}" for _, r in p["new"][:10]], "이름 같은 공고": p["dups"][:10],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="구글 시트(CSV) 공고를 DB에 반영 (기본: 미리보기)")
    ap.add_argument("source", nargs="?", help="CSV 파일 경로(또는 https 주소)")
    ap.add_argument("--url", help="CSV 주소(https). 환경변수 SHEET_CSV_URL 도 가능")
    ap.add_argument("--apply", action="store_true", help="실제로 저장")
    a = ap.parse_args(argv)
    src = a.url or a.source or os.environ.get("SHEET_CSV_URL")
    if not src:
        ap.error("CSV 파일 경로나 --url 이 필요해요.")
    try:
        print(json.dumps(run(src, a.apply), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
