"""제목 판정 모델 비교 시험: 같은 공고 제목에 모델별로 ○/△/× 를 매겨 정답·서로의 일치도·비용·시간을 비교합니다. 저장·삭제는 하지 않습니다.

    # 내장 시험 세트(scripts/judge_samples.tsv, 정답 포함)로 Haiku vs Sonnet
    docker compose exec -T api python -m scripts.judge_compare --models haiku,sonnet
    # Jev도 함께(TypeSafe API 키가 있어야 함: console.typesafe.ai/keys)
    docker compose exec -T -e TYPESAFE_API_KEY=키 api python -m scripts.judge_compare --models haiku,jev
    # 보드에 저장된 공고 300건을 무작위로 뽑아 모델끼리만 비교(정답 없음)
    docker compose exec -T api python -m scripts.judge_compare --models haiku,jev --db 300

모델 이름: haiku(claude-haiku-5-5) · sonnet(claude-sonnet-5-5, 기준용) · jev(jev-latest). Claude 키는 보드 설정에 저장된 것을 씁니다(--email).
"""
import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Job
from scripts import title_judge as tj
from scripts.collect import _ai_key

SAMPLES = Path(__file__).with_name("judge_samples.tsv")
CLAUDE = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}


def load_samples(path: Path = SAMPLES) -> list[dict]:
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        c = ln.split("\t")
        if len(c) >= 3 and c[0] in ("○", "△", "×", "필"):
            out.append({"expected": c[0], "company": c[1], "title": c[2], "note": c[3] if len(c) > 3 else ""})
    return out


def run_model(name: str, items: list[dict], claude_key: str | None, jev_key: str | None, ask=None, post=None) -> dict:
    t0 = time.time()
    if name == "jev":
        r = tj.judge_jev(items, jev_key or "", post=post)
    else:
        r = tj.judge(items, claude_key or "", CLAUDE[name], ask=ask)
    r["seconds"] = round(time.time() - t0, 1)
    return r


def compare(items: list[dict], models: list[str], claude_key=None, jev_key=None, ask=None, post=None, reference: str | None = None) -> dict:
    res = {m: run_model(m, items, claude_key, jev_key, ask, post) for m in models}
    has_gold = all(i.get("expected") for i in items)
    out: dict = {"공고 수": len(items), "모델": {}}
    for m, r in res.items():
        v = [x["v"] for x in r["result"]]
        d = {"○△×": dict(Counter(v)), "비용(USD)": round(r["cost_usd"], 4), "초": r["seconds"], "실패한 묶음": r["failed"]}
        if has_gold:
            d["정답과 일치"] = f"{sum(a == i['expected'] for a, i in zip(v, items))}/{len(items)}"
            d["×를 놓침(×인데 ○/△)"] = sum(1 for a, i in zip(v, items) if i["expected"] == "×" and a != "×")
            d["좋은 공고를 ×로(○인데 ×)"] = sum(1 for a, i in zip(v, items) if i["expected"] == "○" and a == "×")
        out["모델"][m] = d
    ref = reference or models[0]
    for m in models:
        if m != ref:
            a, b = [x["v"] for x in res[ref]["result"]], [x["v"] for x in res[m]["result"]]
            out["모델"][m][f"{ref}와 일치"] = f"{sum(x == y for x, y in zip(a, b))}/{len(items)}"
    diff = []
    for k, it in enumerate(items):
        vs = {m: res[m]["result"][k]["v"] for m in models}
        if len(set(vs.values())) > 1 or (has_gold and any(v != it["expected"] for v in vs.values())):
            diff.append(f"{it.get('expected', '?')} | {it['company']} — {it['title'][:50]} | " + " ".join(f"{m}={v}" for m, v in vs.items()))
    out["서로 다르거나 정답과 다른 공고"] = diff[:80]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="제목 판정 모델 비교(저장·삭제 안 함)")
    ap.add_argument("--models", default="haiku,sonnet", help="쉼표로: haiku · sonnet · jev")
    ap.add_argument("--samples", help="시험 세트 파일(기본 scripts/judge_samples.tsv)")
    ap.add_argument("--db", type=int, metavar="N", help="시험 세트 대신 보드의 공고 N건을 무작위로(정답 없음)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--email")
    a = ap.parse_args(argv)
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    bad = [m for m in models if m not in ("haiku", "sonnet", "jev")]
    if bad:
        ap.error("모델은 haiku · sonnet · jev 중에서")
    init_db()
    if a.db:
        with SessionLocal() as db:
            jobs = db.execute(select(Job)).scalars().all()
        random.Random(a.seed).shuffle(jobs)
        items = [{"company": j.company, "title": j.title} for j in jobs[:a.db]]
    else:
        items = load_samples(Path(a.samples) if a.samples else SAMPLES)
    jev_key = os.environ.get("TYPESAFE_API_KEY")
    if "jev" in models and not jev_key:
        ap.error("Jev를 쓰려면 TYPESAFE_API_KEY 환경변수가 필요해요(docker compose exec -e TYPESAFE_API_KEY=… )")
    ckey = None
    if any(m in CLAUDE for m in models):
        with SessionLocal() as db:
            ckey = _ai_key(db, a.email)
    print(json.dumps(compare(items, models, ckey, jev_key), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
