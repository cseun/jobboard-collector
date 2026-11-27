"""수집기가 읽는 사이트별 '어댑터': 사람인 · 잡코리아 · 원티드.
각 어댑터는 같은 모양으로 (주소 만들기 · 목록 읽기 · 다음 쪽 여부 · 보기 주소)를 제공하고, 수집 틀(collect.py)이 공통으로 씁니다.
읽은 공고는 공통 모양의 dict: id, title, company, loc, career, edu, emp, due_raw, day_kind, day, src

- 사람인: 검색 결과 HTML(등록일순), 상세는 view-detail HTML.
- 잡코리아: 검색 결과 HTML(최신순). 목록에는 등록일·마감이 안 보여서 비워 둠(접수 중으로 저장, 마감 미확인). 상세는 아직 안 읽음.
- 원티드: 검색 JSON API(최신순) + 공고별 JSON(/api/v4/jobs/{id})에서 근무지·마감·자격요건·우대사항을 구조 그대로 받음(AI 비용 0).
"""
import json
import re
import urllib.parse

from bs4 import BeautifulSoup

SARAMIN_PAGE = 40
WANTED_PAGE = 20
REGIONS = r"(서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주|전국|해외)"


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


# ───────────────────────── 사람인 ─────────────────────────
def saramin_url(kw: str, page: int) -> str:
    return f"https://www.saramin.co.kr/zf_user/search/recruit?searchword={urllib.parse.quote(kw)}&recruitSort=reg_dt&recruitPageCount={SARAMIN_PAGE}&recruitPage={page}"


def saramin_parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for it in soup.select(".item_recruit"):
        rid = (it.get("value") or "").strip()
        a = it.select_one(".job_tit a")
        if not rid.isdigit() or a is None:
            continue
        conds = [_txt(s) for s in it.select(".job_condition > span")]
        rest = conds[1:]
        day = _txt(it.select_one(".job_day"))
        m = re.match(r"(등록일|수정일)\s*(\S+)", day)
        cl = it.select_one('.corp_name a[href*="csn="]')
        cm = re.search(r"csn=([^&\s]+)", cl.get("href", "")) if cl else None
        out.append({"id": rid, "csn": cm.group(1) if cm else "", "title": (a.get("title") or _txt(a)).strip(), "company": _txt(it.select_one(".corp_name")),
                    "loc": conds[0] if conds else "", "career": rest[0] if rest else "", "edu": rest[1] if len(rest) >= 2 else "",
                    "emp": rest[-1] if len(rest) >= 3 else "", "due_raw": _txt(it.select_one(".job_date .date")),
                    "day_kind": m.group(1) if m else "", "day": m.group(2) if m else ""})
    return out


def saramin_next(html: str, page: int, n_items: int = 0) -> bool:
    """쪽 번호 링크(a[page=N+1])가 어디에 있든 찾고, 못 찾아도 이번 쪽이 꽉 찼으면(40건) 다음 쪽이 있다고 봅니다."""
    soup = BeautifulSoup(html, "html.parser")
    if any((a.get("page") or "") == str(page + 1) for a in soup.select("a[page]")):
        return True
    return n_items >= SARAMIN_PAGE


# ───────────────────────── 잡코리아 ─────────────────────────
def jobkorea_url(kw: str, page: int) -> str:
    return f"https://www.jobkorea.co.kr/Search/?stext={urllib.parse.quote(kw)}&tabType=recruit&Ord=RegDtDesc&Page_No={page}"


_CAREER = re.compile(r"^(신입·경력|신입|경력무관|경력(?:\s*\d+.*)?)$")
_DATE_REG = re.compile(r"(\d{1,2}/\d{1,2})\s*(?:\([^)]*\))?\s*등록")
_DATE_DUE = re.compile(r"(\d{1,2}/\d{1,2})\s*(?:\([^)]*\))?\s*마감")


LOC_RE = re.compile(rf"^{REGIONS}(?:\s+\S+){{0,3}}$")


def _ids_in(node) -> set[str]:
    return {m.group(1) for a in node.select('a[href*="/Recruit/GI_Read/"]') if (m := re.search(r"GI_Read/(\d+)", a.get("href", "")))}


def jobkorea_parse(html: str) -> list[dict]:
    """잡코리아 검색 결과(서버가 만들어 주는 HTML)에서 공고 카드를 읽습니다. 클래스 이름이 자주 바뀌는 사이트라 이름 대신
    '공고 링크(/Recruit/GI_Read/번호)'와 카드 안 글자 순서로 읽습니다: 제목 → 회사 → (그룹) → 지역 → 직무 → … → 경력.
    카드 = 그 공고 번호만 담고 있는 가장 큰 바깥 상자."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select('a[href*="/Recruit/GI_Read/"]'):
        m = re.search(r"GI_Read/(\d+)", a.get("href", ""))
        if not m or m.group(1) in seen:
            continue
        rid = m.group(1)
        own = [t.strip() for t in a.stripped_strings if t.strip() and t.strip() != "스크랩"]
        if not own:
            continue                                   # 로고만 있는 링크는 건너뜀(글자가 있는 링크 = 제목)
        card = a
        while card.parent is not None and card.parent.name not in ("body", "html", "[document]") and _ids_in(card.parent) <= {rid}:
            card = card.parent
        strings = [t.strip() for t in card.stripped_strings if t.strip() and t.strip() != "스크랩"]
        seen.add(rid)
        title = own[0]                                 # 링크가 카드 전체를 감싸도 첫 글자 덩어리가 제목
        rest = strings[strings.index(title) + 1:] if title in strings else strings[1:]
        company = rest[0] if rest else ""
        loc = next((t for t in rest[1:] if LOC_RE.match(t)), "")
        career = next((t for t in rest if _CAREER.match(t)), "")
        blob = " ".join(strings)
        reg, due = _DATE_REG.search(blob), _DATE_DUE.search(blob)
        out.append({"id": rid, "title": title, "company": company, "loc": loc, "career": career, "edu": "", "emp": "",
                    "due_raw": (due.group(1) + " 마감") if due else ("상시채용" if "상시" in blob else ""),
                    "day_kind": "등록일" if reg else "", "day": reg.group(1) if reg else ""})
    return out


def jobkorea_next(html: str, page: int, n_items: int = 0) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return any(re.search(rf"[?&]Page_No={page + 1}(?:&|$)", a.get("href", "")) for a in soup.select("a[href*=Page_No]")) or n_items >= 20


# ───────────────────────── 원티드 ─────────────────────────
def wanted_url(kw: str, page: int) -> str:
    return (f"https://www.wanted.co.kr/api/chaos/search/v1/position?query={urllib.parse.quote(kw)}&limit={WANTED_PAGE}"
            f"&offset={(page - 1) * WANTED_PAGE}&job_sort=job.latest_order&country=kr&years=-1&locations=all")


def wanted_detail_url(rid: str) -> str:
    return f"https://www.wanted.co.kr/api/v4/jobs/{rid}"


def _wanted_career(af, at) -> str:
    if af in (None, 0):
        return "신입·경력"
    return f"경력 {af}~{at}년" if at and at != af else f"경력 {af}년↑"


def wanted_parse(text: str) -> list[dict]:
    try:
        data = json.loads(text).get("data") or []
    except (ValueError, AttributeError):
        return []
    out = []
    for d in data:
        if not d.get("id") or not d.get("position"):
            continue
        out.append({"id": str(d["id"]), "title": d["position"], "company": (d.get("company") or {}).get("name", ""), "loc": "",
                    "career": _wanted_career(d.get("annual_from"), d.get("annual_to")), "edu": "", "emp": d.get("employment_type") or "",
                    "due_raw": "", "day_kind": "", "day": ""})
    return out


def wanted_next(text: str, page: int, n_items: int = 0) -> bool:
    try:
        return bool((json.loads(text).get("links") or {}).get("next"))
    except (ValueError, AttributeError):
        return False


def wanted_parse_detail(text: str) -> dict:
    """공고 JSON → 근무지·마감(YYYY-MM-DD 또는 빈 글자)·필수·우대·담당 업무·복지. 읽을 수 없으면 빈 dict."""
    try:
        job = json.loads(text).get("job") or {}
    except (ValueError, AttributeError):
        return {}
    det, addr = job.get("detail") or {}, job.get("address") or {}
    loc = (addr.get("location") or addr.get("full_location") or "").strip()
    due = str(job.get("due_time") or "")[:10]
    return {"loc": loc, "due": due if re.match(r"\d{4}-\d{2}-\d{2}$", due) else "", "req": (det.get("requirements") or "").strip(),
            "pref": (det.get("preferred_points") or "").strip(), "tasks": (det.get("main_tasks") or "").strip(),
            "benefits": (det.get("benefits") or "").strip(), "active": job.get("status", "active") == "active"}


class Source:
    def __init__(self, name, key, site, url, parse, nxt, view, detail_url=None):
        self.name, self.key, self.site, self.url, self.parse, self.next, self.view, self.detail_url = name, key, site, url, parse, nxt, view, detail_url


SOURCES = {
    "saramin": Source("saramin", "saramin", "사람인", saramin_url, saramin_parse, saramin_next, lambda i: f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={i}",
                      lambda i: f"https://www.saramin.co.kr/zf_user/jobs/relay/view-detail?rec_idx={i}"),
    "jobkorea": Source("jobkorea", "jobkorea", "잡코리아", jobkorea_url, jobkorea_parse, jobkorea_next, lambda i: f"https://www.jobkorea.co.kr/Recruit/GI_Read/{i}"),
    "wanted": Source("wanted", "wanted", "원티드", wanted_url, wanted_parse, wanted_next, lambda i: f"https://www.wanted.co.kr/wd/{i}", wanted_detail_url),
}


def jobkorea_parse_detail(html: str) -> dict:
    """잡코리아 채용상세의 srcdoc 본문만 읽는다. 바깥 페이지의 추천공고·후기는 제외한다."""
    soup = BeautifulSoup(html, "html.parser")
    ifr = next((f.get("src") for f in soup.select("iframe[src]") if re.search(r"GI_Read|Ifrm|Comt", f.get("src", ""))), "")
    frames = [BeautifulSoup(f.get("srcdoc"), "html.parser") for f in soup.select("iframe[srcdoc]") if f.get("srcdoc")]
    images = []
    parts = []
    for frame in frames:
        for img in frame.select("img[src]"):
            src = img.get("src", "").strip()
            if src:
                images.append("https:" + src if src.startswith("//") else src)
        for t in frame.select("script,style,noscript"):
            t.decompose()
        parts.append(frame.get_text("\n", strip=True))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()
    # 채용상세가 없는 구형 화면도 바깥 페이지 전체를 본문으로 간주하지 않는다.
    lines = [s.strip() for s in soup.get_text("\n", strip=True).splitlines() if s.strip()]
    fields = {}
    for label, key in (("사원수", "사원수"), ("기업구분", "기업형태"), ("산업(업종)", "업종"), ("위치", "주소")):
        try:
            i = lines.index(label, lines.index("기업 정보"))
            fields[key] = lines[i + 1]
        except (ValueError, IndexError):
            pass
    writer = soup.select_one('meta[name="writer"]')
    return {"text": text, "iframe": ifr, "images": images, "is_image": bool(images) and len(text) < 80,
            "company_name": (writer.get("content") or "").strip() if writer else "", "company_fields": fields}


def jobkorea_parse_frame(html: str) -> dict:
    """상세 페이지가 명시적으로 가리킨 구형 iframe 안의 본문만 읽는다."""
    soup = BeautifulSoup(html, "html.parser")
    images = [("https:" + src if src.startswith("//") else src)
              for im in soup.select("img[src]") if (src := im.get("src", "").strip())]
    for tag in soup.select("script,style,noscript"):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True)).strip()
    return {"text": text, "images": images, "is_image": bool(images) and len(text) < 80}


# ───────────────────── 마감 확인(이미 저장한 공고를 다시 볼 때) ─────────────────────
_DUE_META = re.compile(r"마감일\s*[:：]\s*([^,<\"']+)")
_DATE_ANY = re.compile(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})")


def _date_of(s: str):
    from datetime import date
    m = _DATE_ANY.search(s or "")
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        return None


def _meta_due(html: str) -> dict | None:
    """사람인·잡코리아 공고 페이지의 메타 설명에 적힌 '마감일 : …'을 읽습니다. 못 찾으면 None(= 페이지 모양이 바뀌었거나 막힘 화면)."""
    soup = BeautifulSoup(html, "html.parser")
    for m in soup.select('meta[name="description"], meta[property="og:description"]'):
        found = _DUE_META.search(m.get("content") or "")
        if found:
            raw = found.group(1).strip()
            d = _date_of(raw)
            return {"due": d, "kind": "date" if d else ("상시" if "상시" in raw else "채용시" if "채용" in raw or "수시" in raw else raw[:20])}
    return None


def saramin_status(html: str) -> dict | None:
    meta = _meta_due(html)
    if meta is not None:
        return meta
    # 일부 사람인 상세 페이지에는 마감일 메타 태그가 없고 본문의 접수기간만 있다.
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    m = re.search(r"접수기간\s*[:：]?\s*\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}.{0,35}?~\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})", text)
    if m and (due := _date_of(m.group(1))):
        return {"due": due, "kind": "date"}
    return None


def jobkorea_status(html: str) -> dict | None:
    return _meta_due(html)


def wanted_status(text: str) -> dict | None:
    """원티드 공고 JSON: status가 active가 아니면(close·draft) 닫힌 공고."""
    try:
        job = json.loads(text).get("job")
    except (ValueError, AttributeError):
        return None
    if not isinstance(job, dict) or "status" not in job:
        return None
    d = _date_of(str(job.get("due_time") or ""))
    return {"due": d, "kind": "date" if d else "상시", "closed": job["status"] != "active"}


STATUS_CHECK = {
    "saramin": (lambda i: SOURCES["saramin"].view(i), saramin_status),
    "jobkorea": (lambda i: SOURCES["jobkorea"].view(i), jobkorea_status),
    "wanted": (wanted_detail_url, wanted_status),
}


# ───────────────────── 회사 정보(사람인 기업정보 페이지) ─────────────────────
def saramin_company_url(csn: str) -> str:
    return f"https://www.saramin.co.kr/zf_user/company-info/view?csn={urllib.parse.quote(csn, safe='=')}"


def saramin_company_parse(html: str) -> dict | None:
    """사람인 기업정보 페이지 → 보드의 companies 모양 {sum, det, intro, wel}. 요약·상세가 둘 다 없으면 None(막힘 화면·없는 회사).
    sum: 설립·기업형태·사원수(국민연금 출처)·매출액 / det: 업종·대표자명·사업내용·주소·홈페이지 / wel: [[분류, 항목들], …].
    소개글(intro)은 이 페이지 모양에서 안정적으로 찾을 수 없어 비워 둡니다."""
    soup = BeautifulSoup(html, "html.parser")
    summ = [_txt(li) for li in soup.select(".company_summary li.company_summary_item")]
    det = []
    for dl in soup.select(".company_details dl, .company_details_group"):
        for dt in dl.select("dt"):
            dd = dt.find_next_sibling("dd")
            val = re.sub(r"\s*(지도보기|펼쳐보기|접기)\s*$", "", _txt(dd)) if dd else ""
            if val:
                det.append(f"{_txt(dt)} {val}")
    if not summ and not det:
        return None
    wel = []
    for li in soup.select("ul.list_welfare > li.welfare_item"):
        items = [_txt(c) for c in li.select(".cate_item")]
        cat = next((t for t in li.stripped_strings if t.strip()), "")
        if items:
            wel.append([cat, " ".join(items)])
    fields = {}
    for item in det:
        for label in ("사업내용", "업종", "대표자명", "주소", "홈페이지"):
            if item.startswith(label + " "):
                fields[label] = item[len(label) + 1:].strip()
                break
    for item in summ:
        founded = re.search(r"\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*설립", item)
        age = re.search(r"업력\s*\d+년차", item)
        if founded:
            fields["설립일"] = founded.group()
        if age:
            fields["업력"] = age.group()
        if "기업형태" in item:
            fields["기업형태"] = item.replace("기업형태", "").strip()
        if "매출액" in item:
            fields["매출액"] = item.replace("매출액", "").strip()
    headcount = company_headcount(summ)
    if headcount:
        fields["사원수"] = headcount
    if wel:
        fields["복지"] = {category: items for category, items in wel}
    return {"sum": summ, "det": det, "intro": [], "wel": wel, "fields": fields}


def company_headcount(sum_list: list[str]) -> str:
    """요약에서 사원수만: '8 명 출처: 국민연금 사원수' → '8명'."""
    for t in sum_list:
        m = re.match(r"^([\d,]+)\s*명", t)
        if m and "사원수" in t:
            return f"{m.group(1)}명"
    return ""


# ───────────────────────── 사람인 기업 검색 · 소개글 · 뉴스 ─────────────────────────
def saramin_company_search_url(name: str) -> str:
    return f"https://www.saramin.co.kr/zf_user/search/company?searchword={urllib.parse.quote(name)}&searchType=search&real_search_type=company"


def saramin_company_search_parse(html: str) -> list[dict]:
    """기업 검색 결과 → [{csn, name, founded, ceo, industry, addr}]. 번호(csn)가 없는 카드는 건너뜀."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for card in soup.select(".item_corp"):
        a = card.select_one('a[href*="company-info/view?csn="]')
        m = re.search(r"csn=([^&\s\"]+)", a.get("href", "")) if a else None
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        t = _txt(card)
        f = lambda pat: (re.search(pat, t) or [None, ""])[1].strip()
        out.append({"csn": urllib.parse.unquote(m.group(1)), "name": _txt(a),
                    "founded": f(r"설립일\s*(\d{4})년"), "ceo": f(r"대표자명\s*(\S+)"),
                    "industry": f(r"업종\s*(.+?)\s*(?:재무정보|전체 평균연봉|기업주소|$)"), "addr": f(r"기업주소\s*(.+)$")})
    return out


def _norm_co(s: str) -> str:
    return re.sub(r"[\s·\-_.,]|\(주\)|㈜|주식회사|\(유\)|유한회사|\(사\)|\(재\)", "", s or "").lower()


def pick_company(cands: list[dict], name: str, hint: str = "") -> tuple[dict | None, str]:
    """검색 결과에서 같은 회사를 고름. (후보, 이유). 이름이 정확히 같은 회사가 하나면 확정,
    여럿이면 근무지(hint)의 지역·시군이 주소와 맞는 하나만 확정, 그래도 모호하면 (None, '모호')."""
    n = _norm_co(name)
    same = [c for c in cands if _norm_co(c["name"]) == n]
    if len(same) == 1:
        return same[0], "이름 일치"
    if len(same) > 1:
        toks = [t for t in re.split(r"[\s,()·/]+", hint or "") if len(t) >= 2]
        hit = [c for c in same if any(t in c.get("addr", "") for t in toks)]
        if len(hit) == 1:
            return hit[0], "이름 일치 + 근무지 일치"
        return None, f"모호(같은 이름 {len(same)}곳)"
    return None, "일치하는 회사 없음"


def saramin_company_info_url(csn: str) -> str:
    return f"https://www.saramin.co.kr/zf_user/company-info/view-inner-info?csn={urllib.parse.quote(csn, safe='=')}"


def saramin_intro_parse(html: str) -> list[str]:
    """기업정보 안쪽 페이지의 한 줄 소개 + 기업비전 → 보드의 intro 모양(['한 줄 기업비전 본문']). 없으면 []."""
    soup = BeautifulSoup(html, "html.parser")
    tit = body = ""
    for box in soup.select(".company_introduce"):
        t, p = _txt(box.select_one(".tit")), _txt(box.select_one(".txt"))
        if p and not body:
            body = f"{t} {p}".strip()                      # 본문이 있는 상자: '기업비전 안녕하십니까…'
        elif t and not tit:
            tit = t                                        # 본문이 없는 상자: 한 줄 소개
    full = f"{tit} {body}".strip()
    return [full] if full else []


def google_news_url(name: str) -> str:
    q = urllib.parse.quote(f'"{name}" when:3y')
    return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"


def google_news_parse(xml: str, limit: int = 12) -> list[dict]:
    """구글 뉴스 RSS → [{d: 'YYYY-MM-DD', t: 제목, src: 언론사, u: 링크}] (최신순, 같은 제목 제외). RSS가 아니면 []."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, TypeError, ValueError):
        return []
    out, seen = [], set()
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            d = parsedate_to_datetime(it.findtext("pubDate") or "").date().isoformat()
        except Exception:
            d = ""
        if not title or title in seen:
            continue
        seen.add(title)
        out.append({"d": d, "t": title, "src": src, "u": (it.findtext("link") or "").strip()})
    out.sort(key=lambda x: x["d"], reverse=True)
    return out[:limit]
