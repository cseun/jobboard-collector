"""회사 정보(사람인 기업정보 + 위치 좌표)를 서버가 스스로 모읍니다. 기본은 미리보기, --apply 를 붙여야 저장합니다.

    docker compose exec -T api python -m scripts.enrich_companies                    # 미리보기: 어떤 회사를 채울지만 보여 줌(요청 0건)
    docker compose exec -T api python -m scripts.enrich_companies --apply --max 40

1) 보드 공고에 `csn`(사람인 기업 번호)이 있는데 `companies`에 없는 회사 → 사람인 기업정보 페이지를 읽어 {sum, det, intro, wel}로 저장
   (사원수는 국민연금 출처로 페이지에 이미 적혀 있음. 매출액·설립·업종·대표자·주소·복지도 같은 페이지)
2) 새 회사 중 주소가 있고 `geo`에 없으면 OpenStreetMap Nominatim으로 좌표(요청은 1초에 1건 이하, 하루 --geo-max 건)
3) 남은 요청 한도에서, 90일 넘게 안 본 회사를 다시 읽어 갱신(소개글처럼 직접 넣은 칸은 건드리지 않고 sum·det·wel만 바꿈)
4) 같은 회사의 공고에서 `규모·사원수`·`회사 설명·제품`이 비어 있으면 거기에도 채움(이미 값이 있으면 그대로)
- 못 읽은 회사는 `_miss` 날짜를 남겨 30일 동안 다시 시도하지 않음. 사이트가 막으면(403·429) 그날은 멈춤.
- 공공데이터(국민연금·DART)는 쓰지 않음: 사람인 페이지에 국민연금 기준 사원수가 이미 있어 같은 정보를 두 번 받을 필요가 없음.
"""
import argparse
import json
import re
import sys
import time
from datetime import date, timedelta

import httpx
from sqlalchemy import select

from app import gaps
from app.dates import today_kst
from app.db import SessionLocal, init_db
from app.models import CollectRun, Job, SharedData, utcnow
from scripts import collect as co
from scripts.collect_sources import (company_headcount, google_news_parse, google_news_url, pick_company, saramin_company_info_url,
                                     saramin_company_parse, saramin_company_search_parse, saramin_company_search_url, saramin_company_url,
                                     saramin_intro_parse)

MAX_COMPANIES = 40
MAX_GEO = 20
MAX_FIND = 30                                    # 하루에 이름으로 사람인 회사 번호를 찾아볼 회사 수
MAX_NEWS = 30                                    # 하루에 뉴스를 모을 회사 수
NEWS_DAYS = 30                                   # 뉴스는 30일마다 갱신
STALE_DAYS = 90
MISS_DAYS = 30
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "jobboard-personal/1.0 (personal job board; contact: owner)"


def short_name(name: str) -> str:
    """보드의 geo 키 모양: '(주)로보웍스' → '로보웍스'."""
    return re.sub(r"\s+", "", re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사", "", name or "")).strip()


def address_of(det: list[str]) -> str:
    for t in det:
        if t.startswith("주소 "):
            a = t[3:].strip()
            return re.split(r"\s*[,(]", a)[0].strip()          # '전기의길 10, 102호 (성주동…)' → '전기의길 10'까지
    return ""


def geocode(addr: str) -> list | None:
    """[위도, 경도, 'addr'] 또는 None. Nominatim 이용 정책에 맞춰 호출하는 쪽이 1초 이상 간격을 둡니다."""
    r = httpx.get(NOMINATIM, params={"q": addr, "format": "json", "limit": 1, "countrycodes": "kr"}, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    data = r.json()
    return [round(float(data[0]["lat"]), 5), round(float(data[0]["lon"]), 5), "addr"] if data else None


def _shared(db, key: str, default):
    row = db.get(SharedData, key)
    if row is None:
        row = SharedData(key=key, value=default)
        db.add(row)
    return row


def _val(db, key: str) -> dict:
    row = db.get(SharedData, key)
    return dict(row.value or {}) if row is not None and isinstance(row.value, dict) else {}


def plan(db, ref: date, limit: int) -> dict:
    """채울 회사(새 회사 → 오래된 회사 순). {'new': [(csn, 회사명)], 'stale': [...]}
    확인한 날(companiesChecked)·못 읽은 날(companiesMiss)은 보드가 읽는 companies 밖의 별도 칸에 둡니다(보드 화면에 영향 없음)."""
    comps, miss, checked = _val(db, "companies"), _val(db, "companiesMiss"), _val(db, "companiesChecked")
    names: dict[str, str] = {}
    for j in db.execute(select(Job).order_by(Job.id.desc())).scalars():
        csn = (j.raw or {}).get("csn")
        if csn and csn not in names:
            names[csn] = j.company
    new, stale = [], []
    for csn, name in names.items():
        if csn not in comps:
            if csn not in miss or (date.fromisoformat(miss[csn]) + timedelta(days=MISS_DAYS)) <= ref:
                new.append((csn, name))
        elif csn not in checked or (date.fromisoformat(checked[csn]) + timedelta(days=STALE_DAYS)) <= ref:
            stale.append((csn, name))
    stale.sort(key=lambda x: checked.get(x[0], ""))
    return {"new": new[:limit], "stale": stale[:max(0, limit - len(new))], "new_total": len(new), "stale_total": len(stale)}


def fill_jobs(db, csn: str, comp: dict) -> int:
    """같은 회사 공고의 비어 있는 규모·사업 설명 칸만 채움."""
    n, head = 0, company_headcount(comp.get("sum", []))
    biz = next((t[5:].strip() for t in comp.get("det", []) if t.startswith("사업내용 ")), "")
    for j in db.execute(select(Job)).scalars():
        raw = j.raw or {}
        if raw.get("csn") != csn:
            continue
        ch = {}
        if head and str(raw.get("규모·사원수") or "").strip() in ("", "미확인", "-"):
            ch["규모·사원수"] = head
        if biz and str(raw.get("회사 설명·제품") or "").strip() in ("", "미확인", "-"):
            ch["회사 설명·제품"] = biz
        if comp.get("fields") and not raw.get("회사 정보"):
            ch["회사 정보"] = comp["fields"]
            ch["회사 정보 출처"] = saramin_company_url(csn)
        if ch:
            j.raw = {**raw, **ch}
            n += 1
    return n


def find_targets(db, ref: date, limit: int) -> list[tuple[str, str, str]]:
    """번호(csn)가 없는 공고의 회사 → [(회사명, 근무지 힌트, 대표 공고 id…)]. 접수 중 공고가 있는 회사를 먼저, 30일 안에 찾아본 회사는 건너뜀."""
    amb = _val(db, "companiesFind")
    seen: dict[str, tuple[str, str, int]] = {}
    for j in db.execute(select(Job).order_by(Job.id.desc())).scalars():
        raw = j.raw or {}
        key = short_name(j.company)
        if raw.get("csn") or not key or key in seen:
            continue
        done = amb.get(key, {}).get("date")
        if done and date.fromisoformat(done) + timedelta(days=MISS_DAYS) > ref:
            continue
        seen[key] = (j.company, str(raw.get("근무 위치") or j.location or ""), 0 if gaps.is_active(j) else 1)
    ordered = sorted(seen.values(), key=lambda x: x[2])
    return [(c, h, "") for c, h, _ in ordered[:limit]]


def set_csn(db, key: str, csn: str, how: str) -> int:
    n = 0
    for j in db.execute(select(Job)).scalars():
        raw = j.raw or {}
        if short_name(j.company) == key and not raw.get("csn"):
            j.raw = {**raw, "csn": csn, "csn 출처": how}
            n += 1
    return n


def news_targets(db, ref: date, limit: int) -> list[str]:
    """뉴스를 모을 회사(짧은 이름): 접수 중 공고가 있는 회사 중 뉴스가 없거나 30일 지난 순."""
    news = _val(db, "companyNews")
    names: dict[str, str] = {}
    for j in db.execute(select(Job).order_by(Job.id.desc())).scalars():
        if not gaps.is_active(j):
            continue
        k = short_name(j.company)
        if k and k not in names:
            names[k] = j.company
    due = [k for k in names if k not in news or (date.fromisoformat(news[k].get("updated", "2000-01-01")) + timedelta(days=NEWS_DAYS)) <= ref]
    due.sort(key=lambda k: news.get(k, {}).get("updated", ""))
    return due[:limit]


def run(apply: bool = False, max_n: int = MAX_COMPANIES, geo_max: int = MAX_GEO, fetch=None, geo=None, ref: date | None = None, say=print, sleep=None,
        find_max: int = MAX_FIND, news_max: int = MAX_NEWS, fetch_news=None) -> dict:
    ref = ref or today_kst()
    init_db()
    fetch = fetch or co.Fetcher(max_requests=max_n * 2 + find_max + 10).get
    fetch_news = fetch_news or fetch
    geo = geo or geocode
    sleep = sleep if sleep is not None else time.sleep
    out = {"mode": "저장함" if apply else "미리보기(저장 안 함)", "새 회사": 0, "갱신": 0, "못 읽음": 0, "좌표 추가": 0, "공고 칸 채움": 0, "번호 찾음": 0, "번호 모호": 0,
           "소개글 추가": 0, "뉴스 회사": 0, "건너뜀": []}
    with SessionLocal() as db:
        p = plan(db, ref, max_n)
        ft, nt = find_targets(db, ref, find_max), news_targets(db, ref, news_max)
        out["대상"] = {"새 회사": len(p["new"]), "오래된 회사": len(p["stale"]), "새 회사 전체": p["new_total"], "오래된 회사 전체": p["stale_total"],
                      "번호 찾을 회사": len(ft), "뉴스 모을 회사": len(nt)}
        if not apply:
            out["예시"] = [n for _, n in (p["new"] + p["stale"])[:10]]
            say(f"→ 새 회사 {len(p['new'])}건(전체 {p['new_total']}) · 오래된 회사 {len(p['stale'])}건(전체 {p['stale_total']}) · 번호 찾을 회사 {len(ft)} · 뉴스 {len(nt)}. 저장하려면 --apply")
            return out
        rec = CollectRun(day=ref.isoformat(), mode="회사 정보", status="실행 중", options={"max": max_n, "geo_max": geo_max, "find_max": find_max, "news_max": news_max})
        db.add(rec)
        db.commit()
        row = _shared(db, "companies", {})
        comps = dict(row.value or {})
        miss_row, chk_row = _shared(db, "companiesMiss", {}), _shared(db, "companiesChecked", {})
        miss, chk = dict(miss_row.value or {}), dict(chk_row.value or {})
        geo_row = _shared(db, "geo", {})
        geos = dict(geo_row.value or {})
        find_row, news_row, intro_row = _shared(db, "companiesFind", {}), _shared(db, "companyNews", {}), _shared(db, "companiesIntroChecked", {})
        finds, newsd, introd = dict(find_row.value or {}), dict(news_row.value or {}), dict(intro_row.value or {})
        status, n_req, geo_n = "완료", 0, 0
        try:
            # 0) 번호(csn)가 없는 공고의 회사: 이름으로 사람인 기업 검색 → 같은 회사로 확실한 것만 번호를 채움(모호하면 후보만 남김)
            for name, hint, _ in ft:
                key = short_name(name)
                try:
                    n_req += 1
                    cands = saramin_company_search_parse(fetch(saramin_company_search_url(re.sub(r"\(주\)|㈜|주식회사", "", name).strip() or name)))
                except co.Blocked as e:
                    status, rec.blocked = "차단으로 중단", str(e)[:200]
                    out["중단"] = rec.blocked
                    break
                except httpx.HTTPError as e:
                    out["건너뜀"].append(f"{name}: 회사 검색 {type(e).__name__}")
                    continue
                pick, why = pick_company(cands, name, hint)
                if pick:
                    set_csn(db, key, pick["csn"], f"사람인 기업 검색({why})")
                    finds.pop(key, None)
                    out["번호 찾음"] += 1
                else:
                    finds[key] = {"date": ref.isoformat(), "name": name, "reason": why, "후보": [{"csn": c["csn"], "name": c["name"], "addr": c["addr"], "업종": c["industry"]} for c in cands[:5]]}
                    out["번호 모호"] += 1
                db.commit()
            if "중단" not in out and out["번호 찾음"]:
                p = plan(db, ref, max_n)                      # 방금 찾은 번호로 읽을 회사가 늘어남
            for csn, name in ([] if "중단" in out else p["new"] + p["stale"]):
                is_new = csn not in comps
                try:
                    n_req += 1
                    got = saramin_company_parse(fetch(saramin_company_url(csn)))
                except co.Blocked as e:
                    status, rec.blocked = "차단으로 중단", str(e)[:200]
                    out["중단"] = rec.blocked
                    break
                except httpx.HTTPError as e:
                    out["건너뜀"].append(f"{name}: {type(e).__name__}")
                    got = None
                    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code not in (404, 410):
                        continue
                if got is None:
                    out["못 읽음"] += 1
                    (miss if is_new else chk)[csn] = ref.isoformat()         # 못 읽은 새 회사는 30일 뒤에, 못 읽은 오래된 회사는 90일 뒤에 다시(기존 값 유지)
                    continue
                old = comps.get(csn) or {}
                comps[csn] = {**got, "intro": old.get("intro") or got["intro"]}   # 이미 있던 소개글(직접 넣은 값)은 그대로 둠
                chk[csn] = ref.isoformat()
                miss.pop(csn, None)
                out["새 회사" if is_new else "갱신"] += 1
                out["공고 칸 채움"] += fill_jobs(db, csn, comps[csn])
                addr, key = address_of(got["det"]), short_name(name)
                if is_new and addr and key and key not in geos and geo_n < geo_max:
                    geo_n += 1
                    try:
                        sleep(1.1)
                        g = geo(addr)
                        if g:
                            geos[key] = g
                            out["좌표 추가"] += 1
                    except Exception as e:                      # 좌표 실패는 회사 정보 저장을 막지 않음
                        out["건너뜀"].append(f"{name}: 좌표 실패({type(e).__name__})")
                db.commit()
            # 1) 소개글이 비어 있는 회사: 기업정보 안쪽 페이지에서 한 줄 소개 + 기업비전(직접 넣은 소개글은 건드리지 않음). 90일 안에 본 회사는 건너뜀
            todo = [c for c, v in comps.items() if not (v.get("intro") or []) and (c not in introd or date.fromisoformat(introd[c]) + timedelta(days=STALE_DAYS) <= ref)]
            for csn in ([] if "중단" in out else todo[:max_n]):
                try:
                    n_req += 1
                    intro = saramin_intro_parse(fetch(saramin_company_info_url(csn)))
                except co.Blocked as e:
                    status, rec.blocked = "차단으로 중단", str(e)[:200]
                    out["중단"] = rec.blocked
                    break
                except httpx.HTTPError as e:
                    out["건너뜀"].append(f"소개글 {csn[:8]}…: {type(e).__name__}")
                    continue
                introd[csn] = ref.isoformat()                   # 소개글이 없는 회사도 날짜를 남겨 매일 다시 읽지 않음
                if intro:
                    comps[csn] = {**comps[csn], "intro": intro}
                    out["소개글 추가"] += 1
                db.commit()
            # 2) 최근 3년 뉴스(구글 뉴스 RSS, 로그인 없음): 접수 중 공고가 있는 회사. 제목·날짜·언론사·링크만 저장(본문 복사 안 함)
            for key in nt:
                try:
                    items = google_news_parse(fetch_news(google_news_url(key)))
                except co.Blocked:
                    out["건너뜀"].append("뉴스: 요청이 막혀 오늘은 중단")
                    break
                except httpx.HTTPError as e:
                    out["건너뜀"].append(f"{key}: 뉴스 {type(e).__name__}")
                    continue
                newsd[key] = {"updated": ref.isoformat(), "items": items}
                out["뉴스 회사"] += 1
            row.value, geo_row.value, miss_row.value, chk_row.value = comps, geos, miss, chk
            find_row.value, news_row.value, intro_row.value = finds, newsd, introd
            _shared(db, "companiesUpdated", ref.isoformat()).value = ref.isoformat()
            db.commit()
        finally:
            rec.finished_at, rec.status, rec.requests, rec.detail_requests = utcnow(), status, n_req, n_req
            rec.new_found, rec.updated, rec.skipped = out["새 회사"] + out["번호 찾음"], out["갱신"] + out["공고 칸 채움"] + out["소개글 추가"], out["못 읽음"] + out["번호 모호"]
            rec.notes = [f"새 회사 {out['새 회사']}·갱신 {out['갱신']}·못 읽음 {out['못 읽음']}·좌표 {out['좌표 추가']}·공고 칸 {out['공고 칸 채움']}·번호 찾음 {out['번호 찾음']}·모호 {out['번호 모호']}·소개글 {out['소개글 추가']}·뉴스 {out['뉴스 회사']}"] + out["건너뜀"][:19]
            db.commit()
    say(f"→ 새 회사 {out['새 회사']} · 갱신 {out['갱신']} · 못 읽음 {out['못 읽음']} · 좌표 {out['좌표 추가']} · 공고 칸 채움 {out['공고 칸 채움']} · 번호 찾음 {out['번호 찾음']}(모호 {out['번호 모호']}) · 소개글 {out['소개글 추가']} · 뉴스 {out['뉴스 회사']}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="회사 정보 자동 수집(기본: 미리보기)")
    ap.add_argument("--apply", action="store_true", help="실제로 저장(없으면 미리보기)")
    ap.add_argument("--max", type=int, default=MAX_COMPANIES, help=f"하루에 읽을 회사 수(기본 {MAX_COMPANIES})")
    ap.add_argument("--geo-max", type=int, default=MAX_GEO, help=f"하루 좌표 조회 수(기본 {MAX_GEO})")
    ap.add_argument("--find-max", type=int, default=MAX_FIND, help=f"하루에 회사 번호를 이름으로 찾아볼 수(기본 {MAX_FIND})")
    ap.add_argument("--news-max", type=int, default=MAX_NEWS, help=f"하루에 뉴스를 모을 회사 수(기본 {MAX_NEWS})")
    ap.add_argument("--sleep", type=float, nargs=2, default=(3.0, 5.0), metavar=("MIN", "MAX"))
    ap.add_argument("--ambiguous", action="store_true", help="이름으로 번호를 확정하지 못한 회사와 후보 목록만 보여 주고 끝냄")
    a = ap.parse_args(argv)
    if a.ambiguous:
        init_db()
        with SessionLocal() as db:
            for k, v in sorted(_val(db, "companiesFind").items()):
                print(f"{v.get('name', k)} — {v.get('reason')} ({v.get('date')})")
                for c in v.get("후보", []):
                    print(f"    · {c['name']} | {c.get('업종', '')} | {c.get('addr', '')} | csn={c['csn']}")
        return 0
    f = co.Fetcher(sleep=tuple(a.sleep), max_requests=a.max * 2 + a.find_max + 10)
    fn = co.Fetcher(sleep=(1.0, 2.0), max_requests=a.news_max + 5)            # 뉴스는 다른 사이트라 간격을 짧게
    res = run(apply=a.apply, max_n=a.max, geo_max=a.geo_max, fetch=f.get, find_max=a.find_max, news_max=a.news_max, fetch_news=fn.get)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 2 if res.get("중단") else 0


if __name__ == "__main__":
    sys.exit(main())
