"""내 PC의 Chrome이 로그인 상태로 읽어 온 면접 후기·합격 자소서 예시를 서버 자료에 합치는 규칙.
- 후기: 같은 회사 행이 있으면 새 내용을 '[출처 날짜 수집]' 덩어리로 **덧붙이고**(기존 글은 그대로), 없으면 새 행을 만듦. 같은 주소(url)는 한 번만.
- 합격 자소서 예시: 서류 준비 자료(admin:docs)의 ex에 덧붙임. 같은 주소는 한 번만.
- 수집 대상: 내 지원 현황(apps)과 지원 준비 요청(prep)의 회사 중 후기를 최근에 안 모은 곳.
"""
import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .models import Document, SharedData

RECHECK_DAYS = 60
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def norm(name: str) -> str:
    return re.sub(r"[\s·\-_.,]|\(주\)|㈜|주식회사|\(유\)|유한회사", "", name or "").lower()


def _same(a: str, b: str) -> bool:
    x, y = norm(a), norm(b)
    return bool(x and y and (x == y or (min(len(x), len(y)) >= 2 and (x in y or y in x))))


def _shared(db: DBSession, key: str, default):
    row = db.get(SharedData, key)
    if row is None:
        row = SharedData(key=key, value=default)
        db.add(row)
    return row


def targets(db: DBSession, admin_id: int, ref: date, limit: int = 5) -> list[dict]:
    """후기를 모을 회사: 지원 준비 요청 → 내 지원 현황 순. 최근 60일 안에 모은 회사와 마감·탈락 건은 제외."""
    done = (db.get(SharedData, "reviewImports").value if db.get(SharedData, "reviewImports") else {}) or {}
    ivs = (db.get(SharedData, "interviews").value if db.get(SharedData, "interviews") else []) or []
    cands: list[tuple[str, str]] = []
    for d in db.execute(select(Document).where(Document.collection == "prep").order_by(Document.updated_at.desc())).scalars():
        v = d.value or {}
        if v.get("company"):
            cands.append((v["company"], "지원 준비 요청"))
    apps = db.get(Document, (f"data/users/{admin_id}", "apps"))
    for a in ((apps.value or {}).get("v") or []) if apps else []:
        if a.get("기업") and not re.match(r"^(불합격|탈락)", str(a.get("결과") or "")):
            cands.append((a["기업"], "내 지원 현황"))
    out, seen = [], set()
    for name, why in cands:
        k = norm(name)
        if not k or k in seen:
            continue
        seen.add(k)
        last = done.get(k)
        if last and date.fromisoformat(last) + timedelta(days=RECHECK_DAYS) > ref:
            continue
        row = next((r for r in ivs if _same(r.get("기업", ""), name)), None)
        urls = sorted(set(URL_RE.findall(str((row or {}).get("출처") or ""))))
        out.append({"company": name, "why": why, "has_row": row is not None, "known_urls": urls[:40], "last_checked": (row or {}).get("확인일")})
        if len(out) >= limit:
            break
    return out


def merge_interviews(rows: list[dict], company: str, new: list[dict], today: date) -> tuple[list[dict], int]:
    """(합친 목록, 새로 반영한 후기 수). 이미 출처에 있는 주소는 건너뜀."""
    rows = [dict(r) for r in rows]
    idx = next((i for i, r in enumerate(rows) if _same(r.get("기업", ""), company)), None)
    known = set(URL_RE.findall(str(rows[idx].get("출처") or ""))) if idx is not None else set()
    fresh = [n for n in new if n["url"] not in known]
    if not fresh:
        return rows, 0
    stamp = today.isoformat()
    block = lambda key: "\n\n".join(f"**[{n['src']} {stamp} 수집]** {n[key]}" for n in fresh if n.get(key))   # noqa: E731
    links = " · ".join(f"[{n['src']}]({n['url']})" for n in fresh)
    cnt = sum(max(1, int(n.get("n") or 1)) for n in fresh)
    if idx is None:
        rows.append({"기업": company, "후기 직무(연도)": " / ".join(dict.fromkeys(n["role"] for n in fresh if n.get("role")))[:200] or "-",
                     "전형 흐름": block("flow") or "-", "나온 질문": block("qs") or "-", "준비 포인트": block("tips") or "-",
                     "후기": f"후기 {cnt}건(내 PC Chrome 수집)", "출처": links, "확인일": stamp})
    else:
        r = rows[idx]
        for k, key in (("전형 흐름", "flow"), ("나온 질문", "qs"), ("준비 포인트", "tips")):
            b = block(key)
            if b:
                r[k] = f"{r.get(k) or ''}\n\n{b}".strip()
        r["출처"] = f"{r.get('출처') or ''} · {links}".strip(" ·")
        r["후기"] = f"{r.get('후기') or ''} + 추가 {cnt}건".strip()
        r["확인일"] = stamp
    return rows, len(fresh)


def merge_examples(docs: dict, company: str, examples: list[dict], today: date) -> tuple[dict, int]:
    docs = dict(docs)
    name = next((n for n in docs if _same(n, company)), company)
    entry = dict(docs.get(name) or {})
    have = {x.get("url") for x in entry.get("ex") or []}
    fresh = [e for e in examples if e["url"] not in have]
    if not fresh:
        return docs, 0
    entry["ex"] = [*(entry.get("ex") or []), *fresh]
    entry.setdefault("updated", today.isoformat())
    docs[name] = entry
    return docs, len(fresh)


def record_import(db: DBSession, company: str, ref: date) -> None:
    row = _shared(db, "reviewImports", {})
    row.value = {**(row.value or {}), norm(company): ref.isoformat()}
