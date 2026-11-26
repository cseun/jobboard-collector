"""이미지 공고 읽기 비교 시험: 같은 공고를 설정별로 읽어 '예측 절약액 vs 실제 절약액'과 '읽은 내용의 차이'를 기록합니다. DB는 건드리지 않습니다.

    # 서버(~/jobboard): 이미 읽은 이미지 공고 중 8건을 무작위로 뽑아 3가지 설정을 비교(약 $0.5~0.7, 대부분 Sonnet 기준선 비용)
    sudo docker compose exec -T api python -m scripts.image_compare --n 8 --out /tmp/image_compare.md
    # 같은 공고로 다시 하려면 --seed 를 같게, 특정 공고만: --ids 12,34,56

설정(기본 전부): S = Sonnet 기본값 900/40(기준선) · H1~H6 = Haiku 600/150에 think(끔/켬)×effort(기본/low/high) ·
  조각/겹침: H0(900/40) G1(900/150) G2(600/40) H1(600/150) G3(400/40) G4(400/150) G5(600/0) · OCR: T = Tesseract만, TA = Tesseract 글자 → Haiku가 칸 나누기(이미지는 안 보냄)
  일부만: --configs S,H1,T,TA (첫 번째가 기준선).  오래 걸리니 터미널이 끊겨도 되게 nohup으로 돌려도 됨.
- 예측: 호출 전에 조각 크기로 입력 토큰을 어림하고(가로×세로÷750), 출력은 실측 Sonnet 평균(공고당 3,940 토큰)으로 가정해 비용을 계산
- 실제: 응답이 알려 준 토큰으로 계산. 비교 기준은 Sonnet 결과(정답이 아니라 '기준선').
"""
import argparse
import difflib
import re
import shutil
import subprocess
import tempfile
import os
import io
import json
import random
import time
from statistics import mean

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Job
from app.routers import ai as ai_mod
from scripts import collect as co
from scripts.test_image_read import PROMPT, estimate_cost, fetch_image, slice_image

OFF = {"thinking": {"type": "disabled"}}
H, S6 = "claude-haiku-5-5", "claude-sonnet-5-5"


def _eff(e):
    return {"output_config": {"effort": e}}


CONFIGS = {                # think 켬 = thinking 설정을 안 넣은 모델 기본값, effort 기본 = 안 넣음
    "S": {"label": "Sonnet 기본(think 켬·effort 기본) 900/40 [기준선]", "model": S6, "h": 900, "ov": 40, "extra": {}},
    "H0": {"label": "Haiku think 끔·effort 기본 900/40", "model": H, "h": 900, "ov": 40, "extra": OFF},
    "H1": {"label": "Haiku think 끔·effort 기본", "model": H, "h": 600, "ov": 150, "extra": OFF},
    "H2": {"label": "Haiku think 끔·effort low", "model": H, "h": 600, "ov": 150, "extra": {**OFF, **_eff("low")}},
    "H3": {"label": "Haiku think 끔·effort high", "model": H, "h": 600, "ov": 150, "extra": {**OFF, **_eff("high")}},
    "H4": {"label": "Haiku think 켬·effort 기본", "model": H, "h": 600, "ov": 150, "extra": {}},
    "H5": {"label": "Haiku think 켬·effort low", "model": H, "h": 600, "ov": 150, "extra": _eff("low")},
    "H6": {"label": "Haiku think 켬·effort high", "model": H, "h": 600, "ov": 150, "extra": _eff("high")},
}
def _g(label, h, ov, extra=OFF):
    return {"label": label, "model": H, "h": h, "ov": ov, "extra": extra}


CONFIGS.update({
    "G1": _g("Haiku think 끔 900/150", 900, 150), "G2": _g("Haiku think 끔 600/40", 600, 40),
    "G3": _g("Haiku think 끔 400/40", 400, 40), "G4": _g("Haiku think 끔 400/150", 400, 150),
    "G5": _g("Haiku think 끔 600/겹침 없음", 600, 0),
    "T": {"label": "Tesseract만(AI 없음)", "mode": "ocr", "model": "", "h": 2000, "ov": 100, "extra": {}},
    "TA": {"label": "Tesseract 글자 → Haiku가 칸 나누기(think 끔)", "mode": "ocr+ai", "model": H, "h": 2000, "ov": 100, "extra": OFF},
})
CONFIGS.update({      # 안 자르기 / 모델이 줄이지 않고 받는 최대 크기(긴 변 2,576px)로 자르기
    "N1": _g("Haiku 안 자름(원본 그대로)", 0, 0), "N2": {"label": "Sonnet 안 자름(원본 그대로)", "model": S6, "h": 0, "ov": 0, "extra": {}},
    "M1": _g("Haiku 최대 크기 2576/150", 2576, 150), "M2": {"label": "Sonnet 최대 크기 2576/150", "model": S6, "h": 2576, "ov": 150, "extra": {}},
})
DEFAULT_CONFIGS = "S,H0,H1,H2,H3,H4,H5,H6,G1,G2,G3,G4,G5,T,TA"
OCR_LANG = "kor+eng"
TEXT_PROMPT = PROMPT.replace("이 이미지는 채용공고의 상세 내용입니다(세로로 긴 한 장을 위에서부터 잘라 순서대로 보냈습니다). 보이는 글자만 읽어서", "아래는 채용공고 이미지를 OCR(글자 인식)한 글입니다(인식 오류와 줄바꿈 어긋남이 있을 수 있음). 이 글에 있는 내용만으로")

OBS_SONNET_OUT = 2641          # 실측(8건 시험): Sonnet 출력 건당 / 첫 어림은 3,940이었고 실제보다 1.5배 컸음
OBS_HAIKU_OUT = 970            # 실측(8건 시험): Haiku think 끔 출력 건당
PROMPT_TOKENS = 600            # 안내문(PROMPT) 어림
FIELDS = ["필수", "우대", "필요역량", "담당업무", "복지", "제출서류", "기타안내", "읽지못한부분"]


def est_image_tokens(parts) -> int:
    """공식 문서 방식: 28×28px 한 칸이 토큰 1개. Claude 4.7 이상(5.x 포함)은 긴 변 2,576px·이미지당 최대 4,784토큰을 넘으면 줄여서 읽음."""
    import math
    from PIL import Image
    total = 0
    for d, _ in parts:
        w, h = Image.open(io.BytesIO(d)).size
        sc = min(1.0, 2576 / max(w, h))
        w, h = w * sc, h * sc
        total += min(4784, math.ceil(w / 28) * math.ceil(h / 28))
    return total


def ocr_text(urls: list[str], h: int = 2000, ov: int = 100) -> str:
    """Tesseract(kor+eng)로 이미지 글자만 읽습니다. 긴 이미지는 조각내어 읽고 겹친 부분의 같은 줄은 한 번만 남깁니다."""
    exe = shutil.which("tesseract")
    if not exe:
        raise RuntimeError("tesseract가 설치돼 있지 않아요(Dockerfile에 tesseract-ocr, tesseract-ocr-kor 추가 후 --build)")
    lines: list[str] = []
    for u in urls:
        data, media, _ = fetch_image(u)
        for part, _m in slice_image(data, media, h, ov)[0]:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(part)
            try:
                r = subprocess.run([exe, f.name, "stdout", "-l", OCR_LANG, "--psm", "6"], capture_output=True, text=True, timeout=180)
            finally:
                os.unlink(f.name)
            if r.returncode != 0:
                raise RuntimeError(f"tesseract 실패: {r.stderr[:150]}")
            recent = {x.strip() for x in lines[-25:]}
            lines += [ln for ln in r.stdout.splitlines() if ln.strip() and ln.strip() not in recent]
    return "\n".join(lines)


def structure_text(text: str, key: str, model: str, extra: dict) -> dict:
    """OCR 글자를 Haiku에 보내 칸(필수·우대 등)으로 나눕니다. 이미지는 보내지 않습니다."""
    msgs = [{"role": "user", "text": TEXT_PROMPT + "\n\n[OCR 글자]\n" + text}]
    res = ai_mod._call("anthropic", key, model, msgs, [], 4096, extra)
    return {"data": ai_mod._parse_json(res["text"]), "tokens_in": res["in"] or 0, "tokens_out": res["out"] or 0,
            "cost_usd": estimate_cost("anthropic", model, res["in"], res["out"]) or 0.0, "truncated": res["truncated"]}


def _n(x: str) -> str:
    return re.sub(r"\s+", "", x or "")


def covered(item: str, text: str, thr: float = 0.8) -> bool:
    """항목 글자가 OCR 전문 안에 (공백 무시, 80% 이상 이어진 덩어리로) 들어 있는지"""
    it, tx = _n(item), _n(text)
    if not it:
        return True
    if it in tx:
        return True
    m = difflib.SequenceMatcher(None, tx, it, autojunk=False).find_longest_match(0, len(tx), 0, len(it))
    return m.size / len(it) >= thr


def pick(n: int, seed: int, ids: list[int] | None) -> list[dict]:
    init_db()
    with SessionLocal() as db:
        rows = db.execute(select(Job)).scalars().all()
        c = [j for j in rows if (j.raw or {}).get("이미지 주소") and (j.raw or {}).get("AI 읽은 날") and (not ids or j.id in ids)]
        if not ids:
            random.Random(seed).shuffle(c)
            c = c[:n]
        return [{"id": j.id, "company": j.company, "title": j.title, "urls": j.raw["이미지 주소"][:6]} for j in c]


def items(data, f) -> list[str]:
    v = (data or {}).get(f)
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v).strip()] if v and str(v).strip() else []


def match(base: list[str], other: list[str], thr: float = 0.8) -> tuple[int, int]:
    """기준선 항목 중 other에서 비슷한 글(유사도 thr 이상)을 찾은 수, other에만 있는 수"""
    used, hit = set(), 0
    for b in base:
        best, bi = 0.0, -1
        for i, o in enumerate(other):
            if i in used:
                continue
            r = difflib.SequenceMatcher(None, b, o).ratio()
            if r > best:
                best, bi = r, i
        if best >= thr:
            hit += 1
            used.add(bi)
    return hit, len(other) - len(used)


def run(n=8, seed=1, ids=None, email=None, configs=tuple(DEFAULT_CONFIGS.split(",")), out=None, say=print, reader=None, ocr=None, baseline=None) -> str:
    reuse: set[str] = set()
    base_json = None
    if baseline:                       # 지난번 결과(.json)의 기준선 S를 다시 호출하지 않고 그대로 씀(같은 공고로 비교)
        base_json = json.load(open(baseline, encoding="utf-8"))
        posts = base_json["posts"]
        reuse = {"S"} if "S" in base_json["results"] else set()
        if configs[0] != "S" or not reuse:
            raise RuntimeError("--baseline은 첫 설정이 S이고, 지난 결과에 S가 있어야 해요")
    else:
        posts = pick(n, seed, ids)
    if not posts:
        raise RuntimeError("비교할 이미지 공고가 없어요(AI로 읽은 이미지 공고가 DB에 있어야 해요)")
    key = "test"
    if reader is None:
        with SessionLocal() as db:
            key = co._ai_key(db, email)
        reader = co.ai_read_images
    ocr = ocr or ocr_text
    mode = {c: CONFIGS[c].get("mode", "ai") for c in configs}
    say(f"비교 대상 {len(posts)}건 · 설정 {', '.join(configs)}")

    texts: dict[int, str | None] = {}                          # ── 0) OCR(무료)은 한 번만
    if any(m != "ai" for m in mode.values()):
        for p in posts:
            try:
                t0 = time.monotonic()
                texts[p["id"]] = ocr(p["urls"], 2000, 100)
                p["ocr_sec"] = time.monotonic() - t0
            except Exception as e:
                texts[p["id"]] = None
                say(f"  OCR 실패 {p['company']}: {str(e)[:100]}")
        say("  OCR 완료")

    pred = {c: {"in": 0, "out": 0} for c in configs}          # ── 1) 예측
    if reuse:
        pred["S"] = base_json["pred"]["S"]
    for p in posts:
        imgs = [fetch_image(u)[:2] for u in p["urls"]] if any(mode[c] == "ai" and c not in reuse for c in configs) else []
        for c in configs:
            cf = CONFIGS[c]
            if c in reuse:
                continue
            if mode[c] == "ai":
                parts = []
                for d, m in imgs:
                    parts += slice_image(d, m, cf["h"], cf["ov"])[0]
                p.setdefault("n_slices", {})[c] = len(parts)
                pred[c]["in"] += est_image_tokens(parts) + PROMPT_TOKENS
                pred[c]["out"] += OBS_SONNET_OUT if "sonnet" in cf["model"] else OBS_HAIKU_OUT
            else:
                p.setdefault("n_slices", {})[c] = 0
                if mode[c] == "ocr+ai" and texts.get(p["id"]):
                    pred[c]["in"] += int(len(texts[p["id"]]) / 1.5) + PROMPT_TOKENS      # 한글은 대략 1.5자 ≈ 1토큰
                    pred[c]["out"] += OBS_HAIKU_OUT
    pcost = {c: (estimate_cost("anthropic", CONFIGS[c]["model"], pred[c]["in"], pred[c]["out"]) or 0) if mode[c] != "ocr" else 0.0 for c in configs}

    act = {c: {"in": 0, "out": 0, "cost": 0.0, "sec": 0.0, "fail": 0, "trunc": 0} for c in configs}      # ── 2) 실제
    res = {c: {} for c in configs}
    for c in reuse:
        act[c] = dict(base_json["act"][c])
        res[c] = {int(k): v for k, v in base_json["results"][c].items()}
    for i, p in enumerate(posts, 1):
        for c in configs:
            if c in reuse:
                continue
            cf = CONFIGS[c]
            t0 = time.monotonic()
            try:
                if mode[c] == "ai":
                    r = reader(p["urls"], key, cf["model"], cf["h"], cf["ov"], cf["extra"])
                elif texts.get(p["id"]) is None:
                    raise RuntimeError("OCR 글자 없음")
                elif mode[c] == "ocr":
                    r = {"data": {"전문": texts[p["id"]]}, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "truncated": False}
                    act[c]["sec"] += p.get("ocr_sec", 0)
                else:
                    r = structure_text(texts[p["id"]], key, cf["model"], cf["extra"])
                    act[c]["sec"] += p.get("ocr_sec", 0)
            except Exception as e:
                act[c]["fail"] += 1
                res[c][p["id"]] = None
                say(f"  [{i}/{len(posts)}] {c} 실패: {str(e)[:100]}")
                continue
            act[c]["in"] += r["tokens_in"]; act[c]["out"] += r["tokens_out"]; act[c]["cost"] += r["cost_usd"]
            act[c]["sec"] += time.monotonic() - t0; act[c]["trunc"] += 1 if r["truncated"] else 0
            res[c][p["id"]] = r["data"]
        say(f"  [{i}/{len(posts)}] {p['company']} 완료")

    base = configs[0]

    def score(c, f):
        """기준선 항목 수, 찾은 수, 새로 생긴 수(OCR은 None), 비어 있는 공고 수"""
        tb = th = empty = 0
        to = 0 if mode[c] != "ocr" else None
        for p in posts:
            b, o = res[base].get(p["id"]), res[c].get(p["id"])
            if b is None or o is None:
                continue
            bi = items(b, f)
            if mode[c] == "ocr":
                h = sum(1 for x in bi if covered(x, o["전문"]))
                tb += len(bi); th += h
                empty += 1 if (bi and not o["전문"].strip()) else 0
            else:
                oi = items(o, f)
                h, extra = match(bi, oi)
                tb += len(bi); th += h; to += extra
                empty += 1 if (bi and not oi) else 0
        return tb, th, to, empty

    L = ["# 이미지 공고 읽기 비교 결과", f"- 대상 {len(posts)}건(seed {seed}) · 기준선 {CONFIGS[base]['label']}", ""]
    L += ["## 0. 한눈에", "| 설정 | 건당 실제 비용 | 필수·우대 찾음 | 실패 | 잘림 | 평균 시간(초) |", "|---|---|---|---|---|---|"]
    for c in configs:
        tb = th = 0
        for f in ("필수", "우대"):
            x = score(c, f); tb += x[0]; th += x[1]
        L.append(f"| {CONFIGS[c]['label']} | ${act[c]['cost']/len(posts):.4f} | {th}/{tb} ({th/tb*100 if tb else 0:.0f}%) | {act[c]['fail']} | {act[c]['trunc']} | {act[c]['sec']/len(posts):.1f} |")
    L += ["", "## 1. 비용: 예측 vs 실제", "| 설정 | 조각 수(건당) | 예측 입력 | 예측 비용 | 실제 입력 | 실제 출력 | 실제 비용 | 건당 | 예측 오차 |", "|---|---|---|---|---|---|---|---|---|"]
    for c in configs:
        ns = mean(p["n_slices"][c] for p in posts)
        err = (act[c]["cost"] / pcost[c] - 1) * 100 if pcost[c] else 0
        L.append(f"| {CONFIGS[c]['label']} | {ns:.1f} | {pred[c]['in']:,} | ${pcost[c]:.4f} | {act[c]['in']:,} | {act[c]['out']:,} | ${act[c]['cost']:.4f} | ${act[c]['cost']/len(posts):.4f} | {err:+.0f}% |")
    L += ["", "| 비교 | 예측 절약 | 실제 절약 |", "|---|---|---|"]
    for c in configs[1:]:
        ps, as_ = pcost[base] - pcost[c], act[base]["cost"] - act[c]["cost"]
        L.append(f"| {CONFIGS[base]['label']} → {CONFIGS[c]['label']} | ${ps:.4f} ({ps/pcost[base]*100 if pcost[base] else 0:.0f}%) | ${as_:.4f} ({as_/act[base]['cost']*100 if act[base]['cost'] else 0:.0f}%) |")
    L += ["", "## 2. 안정성", "| 설정 | 실패 | 잘림(truncated) | 평균 시간(초) |", "|---|---|---|---|"]
    for c in configs:
        L.append(f"| {CONFIGS[c]['label']} | {act[c]['fail']} | {act[c]['trunc']} | {act[c]['sec']/len(posts):.1f} |")

    L += ["", "## 3. 읽은 내용 차이(기준선 대비. AI는 항목 유사도 0.8↑, OCR은 항목 글자가 전문에 80%↑ 들어 있으면 '찾음')", "| 설정 | 칸 | 기준선 항목 수 | 찾음 | 놓침 | 새로 생김 | 비어 있는 공고 수 |", "|---|---|---|---|---|---|---|"]
    for c in configs[1:]:
        for f in ("필수", "우대", "담당업무", "필요역량"):
            tb, th, to, empty = score(c, f)
            L.append(f"| {CONFIGS[c]['label']} | {f} | {tb} | {th} | {tb-th} | {'-' if to is None else to} | {empty} |")
    L += ["", "## 4. 공고별 상세(필수·우대 항목 수와 놓친 항목)", "| 공고 | 설정 | 필수 | 우대 | 놓친 필수 예시 |", "|---|---|---|---|---|"]
    for p in posts:
        for c in configs:
            d = res[c].get(p["id"])
            miss = ""
            bi = items(res[base].get(p["id"]), "필수") if res[base].get(p["id"]) is not None else []
            if c != base and d is not None and bi:
                if mode[c] == "ocr":
                    lost = [b for b in bi if not covered(b, d["전문"])]
                else:
                    oi = items(d, "필수")
                    lost = [b for b in bi if not any(difflib.SequenceMatcher(None, b, o).ratio() >= 0.8 for o in oi)]
                miss = "; ".join(x[:30] for x in lost[:2])
            nreq = "-" if (d is None or mode[c] == "ocr") else len(items(d, "필수"))
            npref = "-" if (d is None or mode[c] == "ocr") else len(items(d, "우대"))
            L.append(f"| {p['id']} {p['company'][:12]} | {c} | {nreq} | {npref} | {miss} |")
    md = "\n".join(L)
    if out:
        open(out, "w", encoding="utf-8").write(md + "\n")
        open(out.rsplit(".", 1)[0] + ".json", "w", encoding="utf-8").write(json.dumps({"posts": posts, "pred": pred, "act": act, "ocr": {str(k): v for k, v in texts.items()}, "results": {c: {str(k): v for k, v in r.items()} for c, r in res.items()}}, ensure_ascii=False, indent=1))
    say("\n" + md)
    return md


def main(argv=None):
    ap = argparse.ArgumentParser(description="이미지 공고 읽기 설정 비교(예측 vs 실제 비용, 내용 차이)")
    ap.add_argument("--n", type=int, default=8, help="공고 수(기본 8)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ids", help="공고 id를 쉼표로(--n 무시)")
    ap.add_argument("--email")
    ap.add_argument("--configs", default=DEFAULT_CONFIGS, help="비교할 설정(S,H0~H6). 첫 번째가 기준선")
    ap.add_argument("--baseline", help="지난 결과 .json 경로. 기준선 S를 다시 읽지 않고 그 결과·공고를 그대로 써서 Sonnet 비용을 아낌")
    ap.add_argument("--out", help="결과를 저장할 .md 경로(같은 이름 .json에 원본도 저장)")
    a = ap.parse_args(argv)
    run(a.n, a.seed, [int(x) for x in a.ids.split(",")] if a.ids else None, a.email, tuple(a.configs.split(",")), a.out, baseline=a.baseline)


if __name__ == "__main__":
    main()
