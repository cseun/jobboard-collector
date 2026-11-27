"""사람인·잡코리아·원티드 새 공고 수집기(증분 수집). 기본은 미리보기(--dry-run 과 같음)이고, --apply 를 붙여야 저장합니다.

    docker compose exec -T api python -m scripts.collect                       # 미리보기: 몇 건이 새로 들어올지만 셈
    docker compose exec -T api python -m scripts.collect --apply               # 새 공고 저장(제목·조건·등록일·최초 수집)
    docker compose exec -T api python -m scripts.collect --apply --details     # + 새 공고만 상세 페이지를 열어 본문 저장
    docker compose exec -T api python -m scripts.collect --apply --details --ai-images 10   # + 이미지 공고는 AI로 읽기(하루 10건 한도)

동작
  1. 키워드마다 사람인 검색 결과를 '등록일순'으로 한 쪽씩 읽습니다(40건/쪽).
  2. 이미 보드에 있는 공고가 연달아 STOP_KNOWN개 나오면 그 키워드는 거기서 멈춥니다(더 아래는 옛 공고).
  3. 여러 키워드에 걸린 같은 공고는 공고 번호(rec_idx)로 한 건만 셉니다.
  4. 새 공고만 상세 페이지를 엽니다(--details). 본문이 이미지뿐인 공고만 AI를 씁니다(--ai-images 한도 안에서).
  5. 요청 사이에 3~5초 쉽니다. 차단(403·429)이 보이면 즉시 멈춥니다.

저장되는 날짜: '등록일'(사이트 표기가 '등록일'일 때만. '수정일'이면 비워 둠) · '최초 수집'(오늘, 한국 시간).
"""
import argparse
import base64
import json
import random
import re
import sys
import time
import urllib.parse
from collections import Counter
from datetime import date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from app import config, crypto
from app.dates import parse_date, today_kst
from app.db import SessionLocal, init_db
from app.models import CollectRun, Document, Job, SharedData, User, utcnow
from app.routers import ai as ai_mod
from scripts import sync_sheet as ss
from scripts import title_judge as tj
from scripts.collect_sources import (SOURCES, company_headcount, saramin_company_parse,
                                     saramin_company_url, saramin_next, saramin_parse,
                                     saramin_status, wanted_parse_detail)
from scripts.text_requirements import parse_requirements, parse_sections

DETAIL_URL = "https://www.saramin.co.kr/zf_user/jobs/relay/view-detail?rec_idx={id}"
VIEW_URL = "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={id}"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

STOP_KNOWN = 5            # 이미 아는 공고가 연달아 이만큼 나오면 그 키워드는 멈춤
MAX_PAGES = 5             # 키워드당 최대 쪽 수(안전장치)
MAX_REQUESTS = 300        # 한 번 실행에 보낼 최대 요청 수(안전장치)
IMAGE_TEXT_MAX = 150      # 본문 글자가 이만큼보다 적고 그림이 있으면 '이미지 공고'로 봄
BODY_KEEP = 3000          # 저장할 본문 글자 수
AI_MODEL = "claude-haiku-5-5"      # 이미지 공고 읽기 모델(비용 때문에 Haiku. 바꾸려면 --ai-model)
KEYWORDS_FILE = Path(__file__).with_name("keywords.txt")


FETCH_TRIES, RETRY_WAIT = 3, 6.0      # 연결이 끊기면 최대 3번 시도(6초·12초 쉼)


class Blocked(Exception):
    """사이트가 요청을 막는 것으로 보일 때(403·429) — 즉시 멈춥니다."""


# ───────────────────────── 목록 읽기 ─────────────────────────
def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


parse_list = saramin_parse           # 예전 이름(시험·다른 코드가 씀)
has_next = saramin_next


def due_fields(due_raw: str, ref: date) -> dict:
    """'~ 12/09(수)' → due '12/9' · '채용시'·'상시채용' → due_state · '오늘마감'·'내일마감' → 날짜."""
    t = due_raw.strip()
    m = re.search(r"(\d{1,2})/(\d{1,2})", t)
    if m:
        return {"due": f"{int(m.group(1))}/{int(m.group(2))}"}
    if "오늘" in t:
        return {"due": f"{ref.month}/{ref.day}"}
    if "내일" in t:
        d = date.fromordinal(ref.toordinal() + 1)
        return {"due": f"{d.month}/{d.day}"}
    return {"due_state": t} if t else {}


def to_sheet_row(p: dict, ref: date) -> dict:
    """수집한 공고 → sync_sheet 가 받는 줄 모양(같은 저장·중복 규칙을 그대로 씁니다)."""
    src = SOURCES[p.get("src", "saramin")]
    d = {"site": src.site, "url": src.view(p["id"]), "company": p["company"], "title": p["title"], "loc": p["loc"],
         "career": p["career"], "edu": p["edu"], "checked": ref.isoformat(), "gubun": "[신규·수집]", "origin": f"{src.site} 수집"}
    if p.get("_watch"):
        d["_watch"] = p["_watch"]
    if p.get("csn"):
        d["csn"] = p["csn"]                  # 회사 정보 수집이 이 번호로 사람인 기업정보를 찾음
    d.update(due_fields(p["due_raw"], ref))
    if p["day_kind"] == "등록일":          # '수정일'은 등록일이 아니므로 넣지 않음
        d["posted"] = p["day"]
    return d


def company_summary(comp: dict) -> str:
    """기업정보 페이지의 실제 사업내용을 카드의 회사 설명으로 사용한다."""
    fields = comp.get("fields") or {}
    if fields.get("사업내용") or fields.get("업종"):
        return fields.get("사업내용") or fields["업종"]
    det = comp.get("det") or []
    return next((x[5:].strip() for x in det if x.startswith("사업내용 ")), "") or next(
        (x[3:].strip() for x in det if x.startswith("업종 ")), "")


def unrelated_role_reason(title: str, work: str) -> str:
    """명백한 비SW 현장·기계 직무는 회사 업종이나 검색어와 무관하게 제외한다."""
    hay = f"{title} {work}"
    software = re.search(r"SW|S/W|Software|소프트웨어|프로그래밍|코딩|Python|C\+\+|Java|ROS|PLC|MES|LIMS|VLM|\bAI\b|\bAPI\b|알고리즘|Compiler|운영체제|플랫폼", hay, re.I)
    if software:
        return ""
    if re.search(r"감속기|액추에이터|기구\s*설계|기계\s*설계|생산\s*공정\s*관리", hay):
        return "기계·생산 직무"
    if re.search(r"서비스\s*직원|서비스\s*관리|고객\s*응대|현장\s*설치|기술영업", hay):
        return "현장 서비스·설치 직무"
    return ""


def quality_reason(p: dict, d: dict, comp: dict | None, live: dict | None, ref: date) -> str:
    """상세·회사 정보와 실제 마감을 검증한 공고만 저장한다."""
    if p.get("src", "saramin") != "saramin":
        return "지원 사이트 상세 검증 미구현"
    info = d.get("_detail")
    if not info or info.get("is_image"):
        return "상세 본문 없음 또는 이미지 전체 OCR 미완료"
    sections = parse_sections(info.get("text") or "")
    if not sections.get("담당 업무") or not sections.get("필수"):
        return "업무·필수 항목 구분 실패"
    unrelated = unrelated_role_reason(p.get("title", ""), sections["담당 업무"])
    if unrelated:
        return unrelated
    if not live or (live.get("due") and live["due"] < ref) or (
        not live.get("due") and live.get("kind") not in ("상시", "채용시")
    ):
        return "실제 접수 상태 확인 실패 또는 마감"
    if not comp or not company_summary(comp):
        return "회사 사업 정보 없음"
    d["_sections"], d["_company"], d["_live"] = sections, comp, live
    return ""


# ───────────────────────── 상세 읽기 ─────────────────────────
def parse_detail(html: str) -> dict:
    """상세 페이지 → 본문 글자·그림 주소·접수 시작일. 본문이 거의 없고 그림이 있으면 이미지 공고."""
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one(".user_content") or soup.body or soup
    imgs = []
    for im in box.select("img"):
        src = (im.get("src") or im.get("data-src") or "").strip()
        if not src or "blank.png" in src:
            continue
        imgs.append("https:" + src if src.startswith("//") else src)
    for t in box.select("script,style"):
        t.decompose()
    text = re.sub(r"\n{3,}", "\n\n", box.get_text("\n", strip=True))
    m = re.search(r"접수기간\s*[:：]\s*(\d{4}-\d{2}-\d{2})", text)
    return {"text": text, "images": imgs, "start": m.group(1) if m else "", "is_image": len(text) < IMAGE_TEXT_MAX and bool(imgs)}


# ───────────────────────── 가져오기(네트워크) ─────────────────────────
class Fetcher:
    """요청 사이에 3~5초 쉬고, 요청 수를 세고, 차단이 보이면 멈춥니다. 시험에서는 통째로 바꿔 끼웁니다."""

    def __init__(self, sleep=(3.0, 5.0), max_requests=MAX_REQUESTS, client=None):
        self.sleep, self.max, self.n, self._last = sleep, max_requests, 0, 0.0
        self.client = client or httpx.Client(headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}, timeout=20, follow_redirects=True)

    def get(self, url: str) -> str:
        if self.n >= self.max:
            raise Blocked(f"요청 {self.max}건 한도에 도달해 멈춥니다")
        wait = self._last + random.uniform(*self.sleep) - time.monotonic()
        if self.n and wait > 0:
            time.sleep(wait)
        self.n += 1
        r = None
        for attempt in range(1, FETCH_TRIES + 1):          # 서버가 연결을 끊거나 시간이 초과되면 잠깐 쉬고 다시 시도
            try:
                r = self.client.get(url)
                break
            except httpx.TransportError:
                if attempt == FETCH_TRIES:
                    raise
                time.sleep(RETRY_WAIT * attempt)
            finally:
                self._last = time.monotonic()
        if r.status_code in (403, 429):
            raise Blocked(f"사이트가 요청을 막았어요(HTTP {r.status_code}). 잠시 뒤 다시 하세요")
        r.raise_for_status()
        return r.text


# ───────────────────────── 조건 걸러내기 ─────────────────────────
MAX_CAREER = 5                                   # 요구 경력(최소)이 이 연차보다 크면 저장하지 않음. -1이면 끔
EXCLUDE_FILE = Path(__file__).with_name("exclude_words.txt")
SENIOR_FILE = Path(__file__).with_name("senior_words.txt")


def career_min(text: str) -> int | None:
    """'경력3년↑'→3 · '경력 10~17년'→10 · '신입·경력'·'경력무관'→0 · 읽을 수 없으면 None."""
    t = text or ""
    if re.search(r"신입|무관", t):
        return 0
    m = re.search(r"(\d+)\s*(?:~\s*\d+\s*)?년", t)
    return int(m.group(1)) if m else None


def load_exclude(path: Path | None = None) -> list[str]:
    p = path or EXCLUDE_FILE
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")] if p.exists() else []


def load_senior(path: Path | None = None) -> list[str]:
    p = path or SENIOR_FILE
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")] if p.exists() else []


def senior_hit(p: dict, words: list[str]) -> str:
    hay = f"{p.get('title', '')} {p.get('company', '')}".lower()
    return next((w for w in words if w.lower() in hay), "")


def senior_check(flagged: list[tuple[dict, dict]], key: str | None, model: str, ask=None) -> tuple[dict, dict]:
    """직급 확인 대상 공고의 상세 본문을 읽어 팀원도 뽑는지 확인. 반환: ({공고키: 버리는 이유}, 요약). 본문을 못 읽은 공고는 △로 남기고 '직급 확인' 칸에 이유를 적음."""
    texts = [(p, d, ((d.get("_detail") or {}).get("text") or "") if not (d.get("_detail") or {}).get("is_image") else "") for p, d in flagged]
    ready = [(p, d, t) for p, d, t in texts if len(t) >= 80]
    for p, d, t in texts:
        if len(t) < 80:
            d["_rel"], d["_senior_note"] = "△", "상세 미확인(본문을 읽지 못함)"
    drops: dict = {}
    info = {"대상": len(flagged), "상세 확인": len(ready), "상세 미확인": len(flagged) - len(ready), "팀원도 뽑음": 0, "책임급만": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    if ready and key:
        res = tj.judge_senior([{"company": p["company"], "title": p["title"], "text": t} for p, d, t in ready], key, model, ask=ask)
        for (p, d, t), r in zip(ready, res["result"]):
            if r["junior"]:
                info["팀원도 뽑음"] += 1
                d["_senior_note"] = "상세 확인: 팀원급도 채용" if r["w"] != "판정 안 함" else "상세 확인 실패(남김)"
            else:
                info["책임급만"] += 1
                drops[pkey(p)] = "직급 확인: " + (r["w"] or "책임급만 채용")
        info.update(tokens_in=res["tokens_in"], tokens_out=res["tokens_out"], cost_usd=res["cost_usd"])
    elif ready:
        for p, d, t in ready:
            d["_rel"], d["_senior_note"] = "△", "상세 미확인(AI 키 없음)"
    return drops, info


def skip_reason(p: dict, max_career: int = MAX_CAREER, exclude: list[str] | None = None, keep_grad: bool = False) -> str:
    """저장하지 않을 이유(없으면 빈 글자). 내 조건과 한참 먼 공고가 보드를 채우지 않게 합니다."""
    for w in exclude or []:
        if w.lower() in (p["title"] + " " + p["company"]).lower():
            return f"제외 단어 '{w}'"
    c = career_min(p["career"])
    if max_career >= 0 and c is not None and c > max_career:
        return f"경력 {max_career}년 초과 요구"
    if not keep_grad and re.search(r"석사|박사", p["edu"]):
        return "석사·박사 요건"
    return ""


# ───────────────────────── 증분 수집 ─────────────────────────
WATCH_FILE = Path(__file__).with_name("watch_companies.txt")
WATCH_MAX = 10
WATCH_UNSURE = ("불명", "불분명", "불명확", "판단 불가", "알 수 없")


def load_watch(path: Path | None = None) -> list[str]:
    p = path or WATCH_FILE
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")] if p.exists() else []


def _cn(x: str) -> str:
    return re.sub(r"\s|㈜|\(주\)|주식회사|\(.*?\)", "", x or "").lower()


def watch_crawl(names: list[str], known: set[str], fetch, ref: date, sources: list[str], seen: dict, skipped: dict, skip, say=print, max_new: int = WATCH_MAX) -> dict:
    """관심 회사 이름으로 검색해, 제목에 직무 키워드가 없어도 그 회사의 새 공고를 최대 max_new건 더함. 막히면 {'blocked': 이유}."""
    out = {"회사별": {}, "blocked": ""}
    added = 0
    for name in names:
        for sname in sources:
            if added >= max_new:
                return out
            wseen: dict = {}
            r = crawl([name], known, fetch, ref, 5, 1, say, skip=skip, src=SOURCES[sname], seen=wseen, skipped=skipped)
            if r["blocked"]:
                out["blocked"] = r["blocked"]
                return out
            for k, p in wseen.items():
                if k in seen or _cn(name) not in _cn(p["company"]) or added >= max_new:
                    continue
                p["_watch"] = name
                seen[k] = p
                added += 1
                out["회사별"][name] = out["회사별"].get(name, 0) + 1
    return out


def load_keywords(path: Path | None = None) -> list[str]:
    p = path or KEYWORDS_FILE
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def crawl(keywords: list[str], known: set[str], fetch, ref: date, stop_known=STOP_KNOWN, max_pages=MAX_PAGES, say=print, skip=None, src=None, seen=None, skipped=None) -> dict:
    """키워드별로 등록일순 목록을 읽다가 아는 공고가 연달아 나오면 멈춥니다. 한 번 본 공고는 다시 세지 않습니다."""
    src = src or SOURCES["saramin"]
    seen = {} if seen is None else seen              # 이번 실행에서 처음 본 새 공고("사이트:번호" → 공고)
    skipped = {} if skipped is None else skipped     # 조건 때문에 저장하지 않기로 한 공고("사이트:번호" → 이유)
    per_kw, blocked = [], ""
    for kw in keywords:
        st = {"사이트": src.site, "키워드": kw, "읽은 쪽": 0, "목록 건수": 0, "새 공고": 0, "이미 있음": 0, "조건으로 건너뜀": 0, "다른 키워드와 겹침": 0, "멈춘 이유": ""}
        run_known = 0
        try:
            for page in range(1, max_pages + 1):
                html = fetch(src.url(kw, page))
                st["읽은 쪽"] += 1
                items = src.parse(html)
                if not items:
                    st["멈춘 이유"] = "결과 없음"
                    break
                for it in items:
                    st["목록 건수"] += 1
                    key = f"{src.key}:{it['id']}"
                    it["src"] = src.name
                    if key in known:
                        st["이미 있음"] += 1
                        run_known += 1
                    elif key in seen or key in skipped:
                        st["다른 키워드와 겹침"] += 1
                        run_known += 1
                    elif skip and (why := skip(it)):
                        skipped[key] = why
                        st["조건으로 건너뜀"] += 1
                        run_known += 1           # 건너뛴 것도 '볼 필요 없는 공고'라 연속 계산에 넣음
                    else:
                        seen[key] = it
                        st["새 공고"] += 1
                        run_known = 0
                if run_known >= stop_known:
                    st["멈춘 이유"] = f"아는 공고 {stop_known}건 연속"
                    break
                if not src.next(html, page, len(items)):
                    st["멈춘 이유"] = "마지막 쪽"
                    break
            else:
                st["멈춘 이유"] = f"최대 {max_pages}쪽"
        except Blocked as e:
            st["멈춘 이유"] = "⛔ " + str(e)
            blocked = str(e)
        except httpx.HTTPError as e:                  # 재시도 뒤에도 연결 실패 → 이 키워드만 건너뛰고 다음 키워드로
            st["멈춘 이유"] = f"⚠ 네트워크 오류({type(e).__name__}) — 이 키워드는 건너뜀"
        per_kw.append(st)
        say(f"  [{src.site}] {kw}: {st['읽은 쪽']}쪽 · 목록 {st['목록 건수']} · 새 {st['새 공고']} · 아는 {st['이미 있음']} · 건너뜀 {st['조건으로 건너뜀']} · 겹침 {st['다른 키워드와 겹침']} · {st['멈춘 이유']}")
        if blocked:
            break
    return {"per_keyword": per_kw, "new": list(seen.values()), "blocked": blocked, "skipped": skipped}


def known_keys(db) -> set[str]:
    return {ss.canon(j.source_key) for j in db.execute(select(Job)).scalars()}


# ───────────────────────── 제목 판정(AI가 × 로 거른 공고 기억) ─────────────────────────
DROP_COLL, DROP_ID, DROP_KEEP = "collector", "titleDrop", 20000   # 저장하지 않은 공고를 기억해 두면 내일 다시 묻지 않고 '아는 공고'로 셈
JUDGE_MAX = 4000          # 한 번에 판정할 최대 공고 수(안전장치)


def drop_cache(db) -> dict:
    row = db.get(Document, (DROP_COLL, DROP_ID))
    return dict((row.value or {}).get("drop") or {}) if row else {}


def save_drop(db, add: dict, ref: date) -> None:
    if not add:
        return
    row = db.get(Document, (DROP_COLL, DROP_ID))
    cur = dict((row.value or {}).get("drop") or {}) if row else {}
    cur.update(add)
    cur = dict(list(cur.items())[-DROP_KEEP:])
    val = {"drop": cur, "updated": ref.isoformat()}
    if row is None:
        db.add(Document(collection=DROP_COLL, doc_id=DROP_ID, value=val, owner_id=None))
    else:
        row.value = val


def pkey(p: dict) -> str:
    return f"{SOURCES[p.get('src', 'saramin')].key}:{p['id']}"


def apply_judge(new: list[dict], skipped: dict, key: str, model: str = tj.JUDGE_MODEL, max_n: int = JUDGE_MAX, ask=None) -> tuple[list[dict], dict, dict, dict]:
    """새 공고를 제목으로 판정해 ×를 걸러냄. 반환: (남은 새 공고, {키: ○/△}, {키: ×이유}, 요약). 실패하면 남김(△)."""
    target = new[:max_n]
    res = tj.judge(target, key, model, ask=ask)
    keep, rel, drops, ex = [], {}, {}, {"×": [], "△": []}
    for p, r in zip(target, res["result"]):
        k = pkey(p)
        if r["v"] == "×" and p.get("_watch") and (not r["w"] or any(x in r["w"] for x in WATCH_UNSURE)):
            r = {**r, "v": "△", "w": f"관심기업 확인용 — AI: {r['w'] or '직무 불명'}"}      # 제목에 직무가 없어 판단이 안 된 경우만 남김(마케팅·회계처럼 분명한 비대상 직무는 ×)
        if r["v"] == "×":
            drops[k] = r["w"] or "관련 없음"
            skipped[k] = "AI 제목 판정 ×"
            if len(ex["×"]) < 15:
                ex["×"].append(f"{p['company']} — {p['title']}  ({r['w']})")
            continue
        rel[k] = r["v"]
        keep.append(p)
        if r["v"] == "△" and len(ex["△"]) < 10:
            ex["△"].append(f"{p['company']} — {p['title']}  ({r['w']})")
    keep += new[max_n:]                       # 한도를 넘은 건 판정하지 않고 남김
    cnt = Counter(r["v"] for r in res["result"])
    info = {"model": model, "판정한 공고": len(target), "○": cnt.get("○", 0), "△": cnt.get("△", 0), "필": cnt.get("필", 0), "×": cnt.get("×", 0), "판정 안 하고 남김": max(0, len(new) - max_n),
            "실패한 묶음": res["failed"], "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"], "cost_usd": round(res["cost_usd"], 4),
            "× 예시": ex["×"], "△ 예시": ex["△"]}
    return keep, rel, drops, info


# ───────────────────────── 이미지 공고 AI 읽기 ─────────────────────────
def ai_today(db, ref: date) -> int:
    """오늘 AI로 읽은 공고 수(공고에 'AI 읽은 날'이 오늘로 적혀 있는 것)."""
    return sum(1 for j in db.execute(select(Job)).scalars() if (j.raw or {}).get("AI 읽은 날") == ref.isoformat())


def _ai_key(db, email: str | None):
    q = select(User).where(User.email == email.strip().lower()) if email else select(User).where(User.is_admin.is_(True)).order_by(User.id)
    user = db.execute(q).scalars().first()
    if user is None:
        raise RuntimeError("AI를 쓸 계정을 못 찾았어요(--email)")
    row = ai_mod._rows(db, user.id).get("anthropic")
    if row is None:
        raise RuntimeError(f"{user.email} 계정에 Claude(anthropic) 키가 없어요. 보드 ⚙️ 설정 → AI 연결에서 등록해 주세요")
    return crypto.decrypt(user.id, row.ciphertext)


SLICE_H, SLICE_OVERLAP = 900, 150      # 이미지 공고 조각. 비교 시험(11-11) 결과: 겹침 150px이 효과(94%), 높이는 영향 작아 가장 싼 900px로
AI_SETTINGS = {"조각 높이(px)": SLICE_H, "조각 겹침(px)": SLICE_OVERLAP, "max_tokens": 4096, "thinking": "끔(disabled)", "effort": "기본",
               "프롬프트": "2차(구역 제목 따라 읽기)"}


def ai_read_images(urls: list[str], key: str, model: str | None = None, slice_h: int | None = None, overlap: int | None = None, extra: dict | None = None) -> dict:
    """이미지 공고를 조각내어 한 번에 보내고 JSON으로 받습니다(900px 조각 + 150px 겹침, 구역 제목 따라 읽기)."""
    from scripts.test_image_read import PROMPT, estimate_cost, fetch_image, slice_image
    images = []
    for u in urls[:6]:
        data, media, _ = fetch_image(u)
        parts, _ = slice_image(data, media, SLICE_H if slice_h is None else slice_h, SLICE_OVERLAP if overlap is None else overlap)
        images += [{"media_type": m, "data": base64.b64encode(d).decode()} for d, m in parts]
    model = model or AI_MODEL
    msgs = [{"role": "user", "text": PROMPT}]
    if extra is not None:                                # 비교 시험용: 지정한 설정({}이면 모델 기본값) 그대로, 거부되면 오류로 남김
        res = ai_mod._call("anthropic", key, model, msgs, images, 4096, extra)
    else:
        try:                                             # 생각(thinking)을 꺼서 비용·잘림을 줄임 → 모델이 거부하면 설정 없이 다시
            res = ai_mod._call("anthropic", key, model, msgs, images, 4096, {"thinking": {"type": "disabled"}})
        except Exception:
            res = ai_mod._call("anthropic", key, model, msgs, images, 4096)
    return {"data": ai_mod._parse_json(res["text"]), "model": model, "tokens_in": res["in"] or 0, "tokens_out": res["out"] or 0, "images": len(images),
            "cost_usd": estimate_cost("anthropic", model, res["in"], res["out"]) or 0.0, "truncated": res["truncated"]}


def _lines(v) -> str:
    if isinstance(v, list):
        return "\n".join(f"- {x}" for x in v if str(x).strip())
    return str(v or "").strip()


def apply_ai_fields(raw: dict, data: dict, ref: date) -> None:
    """AI가 읽은 이미지 공고 내용을 보드 칸에 넣습니다. 읽지 못한 칸은 건드리지 않습니다."""
    if _lines(data.get("필수")):
        raw["필수"] = _lines(data["필수"])
    if _lines(data.get("우대")):
        raw["우대"] = _lines(data["우대"])
    extra = []
    for k in ("필요역량", "기타안내", "읽지못한부분"):
        if _lines(data.get(k)):
            extra.append(f"[{k}]\n{_lines(data[k])}")
    if _lines(data.get("담당업무")):
        raw["담당 업무(이미지)"] = _lines(data["담당업무"])
    if data.get("전형절차"):
        raw["전형 절차"] = str(data["전형절차"])
    if extra:
        raw["이미지 공고 기타"] = "\n\n".join(extra)
    raw["AI 읽은 날"] = ref.isoformat()


# ───────────────────────── 실행 ─────────────────────────
def run(keywords: list[str] | None = None, apply: bool = False, details: bool = False, ai_images: int = 0, email: str | None = None,
        fetch=None, ref: date | None = None, say=print, stop_known=STOP_KNOWN, max_pages=MAX_PAGES, ai_reader=None, record: bool = True,
        max_career: int = MAX_CAREER, exclude: list[str] | None = None, keep_grad: bool = False, sources: list[str] | None = None,
        judge: bool | None = None, judge_model: str = tj.JUDGE_MODEL, judge_max: int = JUDGE_MAX, judge_ask=None, watch: list[str] | None = None, watch_max: int = WATCH_MAX,
        quality: bool = False) -> dict:
    """한 번 실행. 끝나면(실패해도) 수집 기록(collect_runs)에 한 줄을 남깁니다."""
    ref = ref or today_kst()
    if apply and quality and not details:
        raise ValueError("검증 저장에는 상세 조회(--details)가 필요합니다.")
    sources = sources or ["saramin"]
    keywords = keywords or load_keywords()
    fetch = fetch or Fetcher().get
    init_db()
    cnt = {"list": 0, "detail": 0}

    def counted(url):                       # 사이트로 나간 요청을 목록/상세로 나눠 셉니다(실패한 요청도 셈)
        cnt["detail" if ("view-detail" in url or "/jobs/relay/view?" in url or "company-info/view?" in url or "/api/v4/jobs/" in url) else "list"] += 1
        return fetch(url)

    run_id = None
    if record:
        with SessionLocal() as db:
            rec = CollectRun(day=ref.isoformat(), mode="저장" if apply else "미리보기", status="실행 중", keywords=len(keywords),
                             options={"details": details, "ai_images": ai_images, "stop_known": stop_known, "max_pages": max_pages,
                                     "max_career": max_career, "exclude_words": len(exclude or []), "keep_grad": keep_grad, "sources": sources, "title_judge": (apply if judge is None else judge)})
            db.add(rec)
            db.commit()
            run_id = rec.id
    st = {"status": "오류", "blocked": "", "notes": []}
    out: dict = {}
    try:
        out = _run(keywords, apply, details, ai_images, email, counted, ref, say, stop_known, max_pages, ai_reader, st, max_career, exclude, keep_grad, sources,
                   (apply if judge is None else judge), judge_model, judge_max, judge_ask, watch, watch_max, quality)
        st["status"] = "차단으로 중단" if out.get("중단") else "완료"
        return out
    except Exception as e:
        st["notes"].append(f"오류: {type(e).__name__}: {e}"[:300])
        raise
    finally:
        if run_id is not None:
            with SessionLocal() as db:
                r = db.get(CollectRun, run_id)
                r.finished_at, r.status, r.requests = utcnow(), st["status"], cnt["list"] + cnt["detail"]
                r.list_pages = cnt["list"]
                r.blocked = (out.get("중단") or st["blocked"] or None)
                r.notes = (out.get("메모") or []) + st["notes"] or None
                r.per_keyword = out.get("키워드별")
                r.listed = sum(k["목록 건수"] for k in out.get("키워드별", []))
                r.known = sum(k["이미 있음"] for k in out.get("키워드별", []))
                r.new_found = out.get("새 공고(중복 뺀)", 0)
                r.skipped = sum(out.get("조건으로 건너뜀", {}).values())
                r.saved_new, r.updated = out.get("저장한 새 공고", 0), out.get("기존 공고 갱신", 0)
                r.detail_requests, r.detail_ok = cnt["detail"], out.get("상세 페이지 읽음", 0)
                r.image_postings = out.get("이미지 공고", 0)
                r.ai_reads = out.get("AI로 읽은 이미지 공고", 0)
                ai = out.get("AI 사용") or {}
                r.ai_model, r.ai_settings = ai.get("model"), ai.get("settings")
                r.input_tokens, r.output_tokens, r.cost_usd = ai.get("tokens_in", 0), ai.get("tokens_out", 0), round(ai.get("cost_usd", 0.0), 4)
                db.commit()


def _run(keywords, apply, details, ai_images, email, fetch, ref, say, stop_known, max_pages, ai_reader, st, max_career=MAX_CAREER, exclude=None, keep_grad=False, sources=None,
         judge=False, judge_model=tj.JUDGE_MODEL, judge_max=JUDGE_MAX, judge_ask=None, watch=None, watch_max=WATCH_MAX, quality=False) -> dict:
    sources = sources or ["saramin"]
    with SessionLocal() as db:
        dropped = drop_cache(db)
        known = known_keys(db) | set(dropped)             # AI가 ×로 거른 공고도 '아는 공고'로 셈 → 매일 다시 묻지 않고 멈춤 기준도 그대로 작동
    say(f"사이트 {', '.join(SOURCES[x].site for x in sources)} · 키워드 {len(keywords)}개 · 보드에 있는 공고·거른 공고 {len(known)}건 · 모드: {'저장' if apply else '미리보기(저장 안 함)'}")
    seen: dict = {}
    skipped: dict = {}
    cr = {"per_keyword": [], "blocked": "", "new": [], "skipped": skipped}
    blocked_src: set[str] = set()
    for name in sources:                     # 한 사이트가 막혀도 다른 사이트는 계속(막힌 사실은 기록에 남김)
        r_ = crawl(keywords, known, fetch, ref, stop_known, max_pages, say, skip=lambda p: skip_reason(p, max_career, exclude, keep_grad),
                   src=SOURCES[name], seen=seen, skipped=skipped)
        cr["per_keyword"] += r_["per_keyword"]
        if r_["blocked"]:
            blocked_src.add(name)
            cr["blocked"] = (cr["blocked"] + " / " if cr["blocked"] else "") + f"[{SOURCES[name].site}] {r_['blocked']}"
    if watch and not cr["blocked"]:           # 관심 회사: 키워드 없는 공고도 최대 watch_max건
        wr = watch_crawl(watch, known, fetch, ref, [x for x in sources if x not in blocked_src], seen, skipped,
                         lambda p: skip_reason(p, max_career, exclude, keep_grad), say, watch_max)
        if wr["blocked"]:
            cr["blocked"] = f"[관심기업] {wr['blocked']}"
        say(f"  [관심기업] 추가 {sum(wr['회사별'].values())}건 {wr['회사별']}")
    cr["new"] = list(seen.values())
    st["blocked"] = cr["blocked"]
    new = cr["new"]
    jinfo, jrel, jdrops, jnotes = None, {}, {}, []
    if judge and new:                       # 제목으로 ○/△/× 판정 → ×는 저장하지 않음(저장할 때만 기억해 둠)
        try:
            with SessionLocal() as db:
                jkey = (judge_ask and "test") or _ai_key(db, email)
            new, jrel, jdrops, jinfo = apply_judge(new, cr["skipped"], jkey, judge_model, judge_max, judge_ask)
            say(f"  [제목 판정 {judge_model}] ○ {jinfo['○']} · △ {jinfo['△']} · × {jinfo['×']} · ${jinfo['cost_usd']:.4f}")
        except Exception as e:              # 판정을 못 해도 수집은 계속(전부 남김)
            jnotes.append(f"제목 판정 못 함(전부 남김): {e}"[:200])
            say("  [제목 판정] 못 함 — " + jnotes[-1])
    senior_words = load_senior()
    for p in new:
        p["_senior"] = bool(senior_hit(p, senior_words))
    dist = Counter(("등록 " + parse_date(p["day"], ref).isoformat()) if p["day_kind"] == "등록일" and parse_date(p["day"], ref) else
                   ("수정일 표기(등록일 모름)" if p["day_kind"] == "수정일" else "날짜 표기 없음") for p in new)
    out = {"mode": "저장함" if apply else "미리보기(저장 안 함)", "키워드": len(keywords), "키워드별": cr["per_keyword"], "새 공고(중복 뺀)": len(new), "조건으로 건너뜀": dict(Counter(cr["skipped"].values()).most_common()),
           "등록일 분포": dict(sorted(dist.items(), reverse=True)), "상세 페이지 요청 예정": len(new) if details else 0, "직급 확인 대상": sum(1 for p in new if p.get("_senior")), "중단": cr["blocked"],
           **({"AI 제목 판정": jinfo} if jinfo else {}), "새 공고 예시": [f"{p['company']} — {p['title']}" for p in new[:10]]}
    if not apply:
        say(f"→ 새 공고 {len(new)}건(중복 뺀). 저장하려면 --apply")
        return out

    rows = [to_sheet_row(p, ref) for p in new]
    for p, d in zip(new, rows):
        d["_rel"] = jrel.get(pkey(p), "")
    detail_done = ai_done = image_n = 0
    ai_use = {"model": None, "settings": None, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    notes: list[str] = list(jnotes)
    if jinfo:
        notes.append(f"제목 판정 {jinfo['model']}: ○{jinfo['○']} △{jinfo['△']} ×{jinfo['×']} · 토큰 {jinfo['tokens_in']}/{jinfo['tokens_out']} · ${jinfo['cost_usd']:.4f}")
        ai_use["cost_usd"] += jinfo["cost_usd"]
    with SessionLocal() as db:
        key = None
        used_today = ai_today(db, ref) if ai_images else 0
        for p, d in zip(new, rows):
            if not details or p.get("src") == "jobkorea" or p.get("src", "saramin") in blocked_src:      # 잡코리아는 상세를 아직 읽지 않음(목록 정보만 저장)
                continue
            if p.get("src") == "wanted":
                try:
                    w = wanted_parse_detail(fetch(SOURCES["wanted"].detail_url(p["id"])))
                except Blocked as e:
                    out["중단"] = cr["blocked"] = str(e)
                    blocked_src.add("wanted")
                    continue
                except httpx.HTTPError as e:
                    notes.append(f"{p['company']}: 원티드 상세 읽기 실패({type(e).__name__})")
                    continue
                if not w:
                    continue
                detail_done += 1
                d["_wanted"] = w
                if w["loc"]:
                    d["loc"] = w["loc"]
                if w["due"]:
                    d["due"] = w["due"]
                    d.pop("due_state", None)
                if not w["active"]:
                    d["due_state"] = "지원 마감"
                continue
            try:
                info = parse_detail(fetch(DETAIL_URL.format(id=p["id"])))
            except Blocked as e:
                out["중단"] = cr["blocked"] = str(e)
                blocked_src.add(p.get("src", "saramin"))
                continue
            except httpx.HTTPError as e:
                notes.append(f"{p['company']}: 상세 읽기 실패({type(e).__name__})")
                continue
            detail_done += 1
            d["_detail"] = info
            if not d.get("posted") and info["start"]:
                d["posted"] = info["start"]          # 목록에 등록일이 없으면 접수 시작일로 대신함
            if not info["is_image"]:
                continue
            image_n += 1
            if not ai_images:
                continue
            if used_today + ai_done >= ai_images:
                notes.append(f"{p['company']}: 오늘 AI 한도({ai_images}건)에 도달해 건너뜀")
                continue
            try:
                key = key or (ai_reader and "test") or _ai_key(db, email)
                r = (ai_reader or ai_read_images)(info["images"], key)
                ai_use["model"] = r.get("model") or ai_use["model"] or AI_MODEL
                ai_use["settings"] = r.get("settings") or AI_SETTINGS
                ai_use["tokens_in"] += r.get("tokens_in", 0)
                ai_use["tokens_out"] += r.get("tokens_out", 0)
                ai_use["cost_usd"] += r.get("cost_usd") or 0.0     # 실패·빈 답이어도 이미 쓴 토큰은 기록
                if r["data"]:
                    d["_ai"] = r
                    ai_done += 1
                else:
                    notes.append(f"{p['company']}: AI가 JSON으로 답하지 않음")
            except Exception as e:  # 한 건이 실패해도 나머지는 계속
                notes.append(f"{p['company']}: AI 읽기 실패 — {e}"[:200])
        flagged = [(p, d) for p, d in zip(new, rows) if p.get("_senior")]
        senior_drops: dict = {}
        if flagged:                                          # 책임·팀장급 단어가 있는 공고: 상세를 읽어 팀원도 뽑는지 확인
            try:
                skey = (judge_ask and "test") or _ai_key(db, email)
            except Exception as e:
                skey = None
                notes.append(f"직급 확인에 쓸 AI 키 없음(전부 △로 남김): {e}"[:160])
            senior_drops, sinfo = senior_check(flagged, skey, judge_model, judge_ask)
            out["직급 확인"] = sinfo
            ai_use["cost_usd"] += sinfo["cost_usd"]
            notes.append(f"직급 확인 {len(flagged)}건: 팀원도 뽑음 {sinfo['팀원도 뽑음']} · 책임급만 {sinfo['책임급만']} · 상세 미확인 {sinfo['상세 미확인']}")
            if senior_drops:
                keep = [i for i, p in enumerate(new) if pkey(p) not in senior_drops]
                new, rows = [new[i] for i in keep], [rows[i] for i in keep]
        company_updates = {}
        if quality:
            company_row = db.get(SharedData, "companies")
            known_companies = dict(company_row.value or {}) if company_row else {}
            checked_companies = {}
            approved, rejected = [], Counter()
            for p, d in zip(new, rows):
                info = d.get("_detail")
                csn = p.get("csn")
                if p.get("src", "saramin") != "saramin" or not csn or not info or info.get("is_image"):
                    rejected["지원 사이트·회사 번호·텍스트 상세 확인 불가"] += 1
                    continue
                sections = parse_sections(info.get("text") or "")
                if not sections.get("담당 업무") or not sections.get("필수"):
                    rejected["업무·필수 항목 구분 실패"] += 1
                    continue
                unrelated = unrelated_role_reason(p.get("title", ""), sections["담당 업무"])
                if unrelated:
                    rejected[unrelated] += 1
                    continue
                try:
                    live = saramin_status(fetch(SOURCES["saramin"].view(p["id"])))
                    if not live or (live.get("due") and live["due"] < ref) or (
                        not live.get("due") and live.get("kind") not in ("상시", "채용시")
                    ):
                        rejected["실제 접수 상태 미확인 또는 마감"] += 1
                        continue
                    if csn not in checked_companies:
                        cached = known_companies.get(csn)
                        checked_companies[csn] = cached if cached and cached.get("fields") else saramin_company_parse(fetch(saramin_company_url(csn)))
                    comp = checked_companies[csn]
                except Blocked as e:
                    out["중단"] = str(e)
                    rejected["사이트 차단"] += 1
                    break
                except httpx.HTTPError:
                    rejected["상태·회사 페이지 읽기 실패"] += 1
                    continue
                why = quality_reason(p, d, comp, live, ref)
                if why:
                    rejected[why] += 1
                    continue
                if live.get("due"):
                    d["due"] = live["due"].isoformat()
                    d.pop("due_state", None)
                elif live.get("kind") in ("상시", "채용시"):
                    d["due_state"] = live["kind"]
                company_updates[csn] = comp
                approved.append((p, d))
            new, rows = [p for p, _ in approved], [d for _, d in approved]
            out["품질 검증"] = {"저장 후보": len(new), "제외": dict(rejected)}
        save_drop(db, {**jdrops, **senior_drops}, ref)
        plan = ss.plan([{k: v for k, v in d.items() if not k.startswith("_")} for d in rows], list(db.execute(select(Job)).scalars()), ref)
        extras = {ss.canon(d["url"]): d for d in rows}
        for key_, raw in plan["new"]:
            d = extras.get(ss.canon(key_), {})
            info, ai, w = d.get("_detail"), d.get("_ai"), d.get("_wanted")
            if d.get("_rel"):
                tj.tag_relevance(raw, d["_rel"])
            if d.get("_watch"):
                raw["관심기업"] = d["_watch"]
            if d.get("_senior_note"):
                raw["직급 확인"] = d["_senior_note"]
            if w:                                             # 원티드: 구조화된 칸을 그대로 옮김(AI·규칙 파서 없이)
                for k, v in (("필수", w["req"]), ("우대", w["pref"])):
                    if v:
                        raw[k] = v
                if w["req"] or w["pref"]:
                    raw["필수·우대 출처"] = "원티드 API"
                raw["원문 본문"] = "\n\n".join(x for x in (w["tasks"], w["req"], w["pref"], w["benefits"]) if x)[:BODY_KEEP]
            if info:
                raw["원문 본문"] = info["text"] if not info["is_image"] else ""
                if not info["is_image"]:                       # 텍스트 공고: 규칙으로 필수·우대를 나눔(못 찾으면 빈칸으로 남김)
                    req = parse_requirements(info["text"])
                    for k, v in req.items():
                        raw[k] = v
                    if req:
                        raw["필수·우대 출처"] = "규칙 파서"
                raw["이미지 공고"] = bool(info["is_image"])
                if info["is_image"]:
                    raw["이미지 주소"] = info["images"][:6]     # 한도 때문에 못 읽은 공고를 다음 날 다시 읽을 때 씀
            if ai:
                apply_ai_fields(raw, ai["data"], ref)
            if d.get("_sections"):
                raw.update(d["_sections"])
                raw["상세 확인일"] = ref.isoformat()
            if d.get("_company"):
                raw["회사 설명·제품"] = company_summary(d["_company"])[:300]
                raw["규모·사원수"] = company_headcount(d["_company"].get("sum") or [])
                raw["회사 정보"] = d["_company"].get("fields") or {}
                raw["회사 정보 출처"] = saramin_company_url(d["csn"])
        ss.apply_plan(db, plan)
        if company_updates:
            row = db.get(SharedData, "companies")
            if row is None:
                row = SharedData(key="companies", value={})
                db.add(row)
            row.value = {**(row.value or {}), **company_updates}
        db.commit()
        backlog = 0
        if details and ai_images and "saramin" not in blocked_src:
            backlog, extra_ai = _ai_backlog(db, fetch, ref, ai_images - used_today - ai_done, key, email, ai_reader, ai_use, notes)
            ai_done += extra_ai
            db.commit()
    out.update({"밀린 이미지 공고 처리": backlog, "저장한 새 공고": len(plan["new"]), "기존 공고 갱신": len(plan["upd"]), "상세 페이지 읽음": detail_done, "이미지 공고": image_n,
                "AI로 읽은 이미지 공고": ai_done, "AI 사용": ai_use, "메모": notes[:20]})
    say(f"→ 새 공고 {len(plan['new'])}건 저장 · 기존 {len(plan['upd'])}건 갱신 · 상세 {detail_done}건 · 이미지 공고 {image_n}건 · AI {ai_done}건"
        + (f" · 토큰 {ai_use['tokens_in']}/{ai_use['tokens_out']} · ${ai_use['cost_usd']:.4f}" if ai_use["model"] else ""))
    return out


# ───────────────────────── 내 PC Chrome이 읽어 보낸 목록(원티드) ─────────────────────────
def compact_to_item(c: dict) -> dict:
    """PC Chrome이 보낸 핵심 칸 → 원티드 목록 파서가 돌려주는 것과 같은 모양."""
    from scripts.collect_sources import _wanted_career
    return {"id": str(c["id"]), "title": c["t"], "company": c.get("c") or "", "loc": "", "career": _wanted_career(c.get("f"), c.get("to")), "edu": "",
            "emp": c.get("e") or "", "due_raw": "", "day_kind": "", "day": ""}


def import_listing(source: str, pages: list[dict], apply: bool = False, email: str | None = None, ref: date | None = None, say=print,
                   max_career: int = MAX_CAREER, exclude: list[str] | None = None, keep_grad: bool = False,
                   judge: bool | None = None, judge_model: str = tj.JUDGE_MODEL, judge_max: int = JUDGE_MAX, judge_ask=None, compact: list[dict] | None = None) -> dict:
    """사이트가 서버를 막을 때(원티드 403) 내 PC Chrome이 읽어 온 목록 JSON을 받아 같은 규칙(아는 공고·조건·제목 판정·저장)으로 처리합니다.
    pages: [{keyword, page, text(사이트가 준 JSON 글자 그대로)}]. 상세 페이지는 읽지 않고 목록 정보만 저장합니다."""
    ref = ref or today_kst()
    init_db()
    src = SOURCES[source]
    judge = apply if judge is None else judge
    exclude = load_exclude() if exclude is None else exclude
    items = [it for pg in pages for it in src.parse(pg.get("text") or "")] + [compact_to_item(c) for c in (compact or [])]
    seen: dict = {}
    skipped: dict = {}
    n_known = n_dup = 0
    with SessionLocal() as db:
        known = known_keys(db) | set(drop_cache(db))
    for it in items:
        it["src"] = src.name
        k = pkey(it)
        if k in known:
            n_known += 1
        elif k in seen or k in skipped:
            n_dup += 1
        elif why := skip_reason(it, max_career, exclude, keep_grad):
            skipped[k] = why
        else:
            seen[k] = it
    new = list(seen.values())
    jinfo, jrel, jdrops, note = None, {}, {}, ""
    if judge and new:
        try:
            with SessionLocal() as db:
                jkey = (judge_ask and "test") or _ai_key(db, email)
            new, jrel, jdrops, jinfo = apply_judge(new, skipped, jkey, judge_model, judge_max, judge_ask)
        except Exception as e:
            note = f"제목 판정 못 함(전부 남김): {e}"[:200]
    sw = load_senior()
    senior_keys = {pkey(p) for p in new if senior_hit(p, sw)}          # 상세를 못 읽는 경로라 확인하지 못함 → 저장하되 △로 표시
    out = {"mode": "저장함" if apply else "미리보기(저장 안 함)", "사이트": src.site, "받은 쪽": len(pages), "직급 확인 대상(상세 미확인으로 △)": len(senior_keys), "받은 핵심 칸 줄": len(compact or []), "목록 건수": len(items), "이미 있음": n_known, "중복": n_dup,
           "조건으로 건너뜀": dict(Counter(skipped.values()).most_common()), "새 공고": len(new), **({"AI 제목 판정": jinfo} if jinfo else {}),
           **({"메모": [note]} if note else {}), "새 공고 예시": [f"{p['company']} — {p['title']}" for p in new[:10]]}
    saved = upd = 0
    if apply:
        rows = [to_sheet_row(p, ref) for p in new]
        with SessionLocal() as db:
            plan = ss.plan(rows, list(db.execute(select(Job)).scalars()), ref)
            rel = {ss.canon(d["url"]): ("△" if pkey(p) in senior_keys else jrel.get(pkey(p), "")) for p, d in zip(new, rows)}
            snr = {ss.canon(d["url"]) for p, d in zip(new, rows) if pkey(p) in senior_keys}
            for key_, raw in plan["new"]:
                if rel.get(ss.canon(key_)):
                    tj.tag_relevance(raw, rel[ss.canon(key_)])
                if ss.canon(key_) in snr:
                    raw["직급 확인"] = "상세 미확인(PC 수집은 목록만)"
            ss.apply_plan(db, plan)
            save_drop(db, jdrops, ref)
            saved, upd = len(plan["new"]), len(plan["upd"])
            db.add(CollectRun(day=ref.isoformat(), mode="저장", status="완료", finished_at=utcnow(), keywords=len({pg.get("keyword") for pg in pages}),
                              options={"sources": [f"{source}(내 PC Chrome)"], "title_judge": bool(judge)}, list_pages=len(pages), requests=0,
                              listed=len(items), known=n_known, new_found=len(seen), skipped=len(skipped), saved_new=saved, updated=upd,
                              ai_model=(jinfo or {}).get("model"), input_tokens=(jinfo or {}).get("tokens_in", 0), output_tokens=(jinfo or {}).get("tokens_out", 0),
                              cost_usd=(jinfo or {}).get("cost_usd", 0.0), notes=[note] if note else None))
            db.commit()
        out.update({"저장한 새 공고": saved, "기존 공고 갱신": upd})
    return out


AI_TRIES = 3     # 한 공고를 AI로 읽다 실패해도 이만큼만 다시 시도


def _ai_backlog(db, fetch, ref, remaining: int, key, email, ai_reader, ai_use: dict, notes: list) -> tuple[int, int]:
    """하루 한도 때문에 못 읽은 이미지 공고를 남은 한도 안에서 읽습니다(접수 중인 것만, 최신 공고 먼저). (처리 시도 수, 성공 수)."""
    if remaining <= 0:
        return 0, 0
    todo = [j for j in db.execute(select(Job).order_by(Job.id.desc())).scalars()
            if (j.raw or {}).get("이미지 공고") is True and not j.raw.get("AI 읽은 날") and j.raw.get("AI 시도", 0) < AI_TRIES
            and not str(j.status or "").startswith("마감") and "saramin:" in ss.canon(j.source_key)]
    tried = ok = 0
    for j in todo[:remaining]:
        raw = dict(j.raw)
        raw["AI 시도"] = raw.get("AI 시도", 0) + 1       # 실패해도 횟수를 남겨 무한 반복을 막음
        tried += 1
        try:
            urls = raw.get("이미지 주소")
            if not urls:
                urls = parse_detail(fetch(DETAIL_URL.format(id=ss.canon(j.source_key).split(":")[1])))["images"]
            key = key or (ai_reader and "test") or _ai_key(db, email)
            r = (ai_reader or ai_read_images)(urls, key)
            ai_use["model"] = r.get("model") or ai_use["model"] or AI_MODEL
            ai_use["settings"] = r.get("settings") or AI_SETTINGS
            ai_use["tokens_in"] += r.get("tokens_in", 0)
            ai_use["tokens_out"] += r.get("tokens_out", 0)
            ai_use["cost_usd"] += r.get("cost_usd") or 0.0
            if r["data"]:
                apply_ai_fields(raw, r["data"], ref)
                ok += 1
            else:
                notes.append(f"{j.company}: (밀린 공고) AI가 JSON으로 답하지 않음")
        except Blocked:
            raise
        except Exception as e:
            notes.append(f"{j.company}: (밀린 공고) AI 읽기 실패 — {e}"[:200])
        j.raw = raw
    return tried, ok


def report(limit: int = 10, say=print) -> list[dict]:
    """최근 실행 기록을 표로 보여 줍니다."""
    init_db()
    with SessionLocal() as db:
        rows = db.execute(select(CollectRun).order_by(CollectRun.id.desc()).limit(limit)).scalars().all()
    for r in rows:
        ai = f" · AI {r.ai_reads}건 {r.ai_model} 토큰 {r.input_tokens}/{r.output_tokens} ${r.cost_usd:.4f}" if r.ai_model else ""
        say(f"#{r.id} {r.day} [{r.mode}·{r.status}] 요청 {r.requests}(목록 {r.list_pages}·상세 {r.detail_requests}) · 목록 {r.listed}건 중 새 {r.new_found} → 저장 {r.saved_new}"
            f"/갱신 {r.updated} · 상세 {r.detail_ok} · 이미지 공고 {r.image_postings}{ai}" + (f" · ⛔ {r.blocked}" if r.blocked else ""))
    return [{"id": r.id, "day": r.day, "status": r.status} for r in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="사람인·잡코리아·원티드 새 공고 증분 수집(기본: 미리보기)")
    ap.add_argument("--apply", action="store_true", help="실제로 저장(없으면 미리보기)")
    ap.add_argument("--dry-run", action="store_true", help="미리보기(기본값과 같음)")
    ap.add_argument("--details", action="store_true", help="새 공고만 상세 페이지를 열어 본문을 저장")
    ap.add_argument("--ai-images", type=int, default=0, metavar="N", help="이미지 공고를 AI(Claude)로 읽기. 하루 최대 N건")
    ap.add_argument("--email", help="AI 키를 가진 계정 이메일(없으면 첫 관리자)")
    ap.add_argument("--keywords", help="쉼표로 구분한 키워드(없으면 scripts/keywords.txt)")
    ap.add_argument("--keywords-file", help="키워드 파일(한 줄에 하나)")
    ap.add_argument("--report", type=int, nargs="?", const=10, metavar="N", help="저장 없이 최근 실행 기록 N개(기본 10)를 보여 줌")
    ap.add_argument("--max-career", type=int, default=MAX_CAREER, metavar="N", help=f"요구 경력이 N년을 넘는 공고는 저장 안 함(기본 {MAX_CAREER}, -1이면 끔)")
    ap.add_argument("--exclude-words", help="제목·회사에 이 단어가 있으면 저장 안 함(쉼표 구분). 없으면 scripts/exclude_words.txt")
    ap.add_argument("--keep-grad", action="store_true", help="석사·박사 요건 공고도 저장")
    ap.add_argument("--source", default="saramin", help="saramin · jobkorea · wanted · all (쉼표로 여러 개 가능, 기본 saramin)")
    ap.add_argument("--judge", dest="judge", action="store_true", default=None, help="저장 전에 제목을 AI(가벼운 모델)로 ○/△/× 판정해 ×를 거름. 미리보기에서도 쓰려면 이 옵션(저장 모드는 기본 켜짐)")
    ap.add_argument("--no-judge", dest="judge", action="store_false", help="제목 판정을 끔")
    ap.add_argument("--judge-model", default=tj.JUDGE_MODEL, help=f"제목 판정 모델(기본 {tj.JUDGE_MODEL})")
    ap.add_argument("--judge-max", type=int, default=JUDGE_MAX, help=f"한 번에 판정할 최대 공고 수(기본 {JUDGE_MAX})")
    ap.add_argument("--watch-file", help="관심 회사 목록 파일(한 줄에 하나, 없으면 scripts/watch_companies.txt)")
    ap.add_argument("--watch-max", type=int, default=WATCH_MAX, help=f"관심 회사의 키워드 없는 공고를 한 번에 최대 몇 건 더할지(기본 {WATCH_MAX})")
    ap.add_argument("--ai-model", default=AI_MODEL, help=f"이미지 공고를 읽을 모델(기본 {AI_MODEL}). 정확도가 아쉬우면 claude-sonnet-5-5")
    ap.add_argument("--slice-h", type=int, default=SLICE_H, help=f"이미지 공고 조각 높이(px, 기본 {SLICE_H})")
    ap.add_argument("--slice-overlap", type=int, default=SLICE_OVERLAP, help=f"조각 위아래 겹침(px, 기본 {SLICE_OVERLAP})")
    ap.add_argument("--no-watch", action="store_true", help="관심 회사 수집을 끔")
    ap.add_argument("--stop-known", type=int, default=STOP_KNOWN)
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    ap.add_argument("--sleep", type=float, nargs=2, default=(3.0, 5.0), metavar=("MIN", "MAX"), help="요청 사이 쉬는 초")
    a = ap.parse_args(argv)
    globals()["AI_MODEL"] = a.ai_model
    globals()["SLICE_H"], globals()["SLICE_OVERLAP"] = a.slice_h, a.slice_overlap
    AI_SETTINGS["조각 높이(px)"], AI_SETTINGS["조각 겹침(px)"] = a.slice_h, a.slice_overlap
    if a.report is not None:
        report(a.report)
        return 0
    srcs = list(SOURCES) if a.source.strip() == "all" else [x.strip() for x in a.source.split(",") if x.strip()]
    bad = [x for x in srcs if x not in SOURCES]
    if bad:
        ap.error(f"모르는 사이트: {', '.join(bad)} (saramin · jobkorea · wanted · all)")
    kws = [k.strip() for k in a.keywords.split(",") if k.strip()] if a.keywords else load_keywords(Path(a.keywords_file) if a.keywords_file else None)
    f = Fetcher(sleep=tuple(a.sleep), max_requests=a.max_requests)
    saving = a.apply and not a.dry_run
    res = run(kws, apply=saving, details=a.details or saving, ai_images=a.ai_images, email=a.email, fetch=f.get,
              stop_known=a.stop_known, max_pages=a.max_pages, max_career=a.max_career, keep_grad=a.keep_grad, sources=srcs,
              judge=a.judge, judge_model=a.judge_model, judge_max=a.judge_max,
              watch=[] if a.no_watch else load_watch(Path(a.watch_file) if a.watch_file else None), watch_max=a.watch_max,
              exclude=[w.strip() for w in a.exclude_words.split(",") if w.strip()] if a.exclude_words else load_exclude(), quality=saving)
    res["요청 수"] = f.n
    print(json.dumps({k: v for k, v in res.items() if k != "키워드별"}, ensure_ascii=False, indent=2))
    return 2 if res.get("중단") else 0


if __name__ == "__main__":
    sys.exit(main())
