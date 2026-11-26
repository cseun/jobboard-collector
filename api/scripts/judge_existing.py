"""이미 보드에 있는 공고를 제목으로 ○/△/× 판정해 공고의 `관련도` 칸에 적어 둡니다. **지우지 않습니다.** 기본은 미리보기(저장 안 함).

    docker compose exec -T api python -m scripts.judge_existing                 # 미리보기: ○△× 건수와 ×·△ 목록
    docker compose exec -T api python -m scripts.judge_existing --apply         # 관련도 칸에 기록(이미 있는 것은 건너뜀, --redo면 다시)
    docker compose exec -T api python -m scripts.judge_existing --max 300       # 한 번에 최대 300건만
    docker compose exec -T api python -m scripts.judge_existing --redo --recheck  # ×로 나온 것만 Sonnet이 한 번 더 확인

- 판정 기준·모델은 새 공고 판정과 같습니다(scripts/title_judge.py, 기본 claude-haiku-5-5).
- ×로 나온 공고를 보고 → 맞으면 보드에서 숨기거나(후속) 지우고, 틀리면 title_judge.PROFILE을 고칩니다.
- AI가 실패하면 그 묶음은 △로 남기고, △는 기록하지 않아 다음에 다시 시도합니다.
"""
import argparse
import json
import sys

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Job
from scripts import title_judge as tj
from scripts.collect import _ai_key

SHOW = 80


def run(apply: bool = False, max_n: int = 4000, redo: bool = False, email: str | None = None, model: str = tj.JUDGE_MODEL, ask=None, recheck: str | None = None, recheck_ask=None) -> dict:
    init_db()
    with SessionLocal() as db:
        jobs = [j for j in db.execute(select(Job).order_by(Job.id)).scalars() if redo or not (j.raw or {}).get("관련도")][:max_n]
        if not jobs:
            return {"mode": "저장함" if apply else "미리보기(저장 안 함)", "판정한 공고": 0}
        key = (ask and "test") or _ai_key(db, email)
        res = tj.judge([{"company": j.company, "title": j.title} for j in jobs], key, model, ask=ask)
        rechecked = None
        if recheck:                                    # ×로 나온 것만 더 강한 모델이 한 번 더 확인 → 그 판정을 따름(오판 줄이기)
            idx = [i for i, r in enumerate(res["result"]) if r["v"] == "×"]
            if idx:
                r2 = tj.judge([{"company": jobs[i].company, "title": jobs[i].title} for i in idx], key, recheck, ask=recheck_ask or ask)
                changed = []
                for i, r in zip(idx, r2["result"]):
                    if r["w"] != "판정 안 함" and r["v"] != "×":
                        changed.append(f"[{jobs[i].id}] {jobs[i].company} | {(jobs[i].title or '')[:50]} → {r['v']} ({r['w']})")
                        res["result"][i] = r
                rechecked = {"모델": recheck, "다시 본 ×": len(idx), "×가 아니게 됨": len(changed), "실패한 묶음": r2["failed"],
                             "비용(USD)": round(r2["cost_usd"], 4), "바뀐 목록(최대 80)": changed[:SHOW]}
        out = {"mode": "저장함" if apply else "미리보기(저장 안 함)", "판정한 공고": len(jobs), "실패한 묶음": res["failed"], "model": model,
               "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"], "cost_usd": round(res["cost_usd"], 4)}
        if res.get("실패 이유"):
            out["실패 이유"] = res["실패 이유"]
        cnt = {"○": 0, "△": 0, "필": 0, "×": 0}
        xs, ts, fs = [], [], []
        for j, r in zip(jobs, res["result"]):
            cnt[r["v"]] += 1
            line = f"[{j.id}] {j.company} | {(j.title or '')[:60]} ({r['w']})"
            if r["v"] == "×":
                xs.append(line)
            elif r["v"] == "△":
                ts.append(line)
            elif r["v"] == "필":
                fs.append(line)
            if apply and r["w"] != "판정 안 함":            # 판정 실패(안 함)는 기록하지 않아 다음에 다시 시도
                raw = tj.tag_relevance(dict(j.raw or {}), r["v"])
                if r["v"] != "○":
                    raw["관련도 이유"] = r["w"]
                else:
                    raw.pop("관련도 이유", None)
                j.raw = raw
        if apply:
            db.commit()
        if rechecked:
            out["× 재확인"] = rechecked
        out.update({"○": cnt["○"], "△": cnt["△"], "필(필드엔지니어)": cnt["필"], "×": cnt["×"], f"× 목록(최대 {SHOW})": xs[:SHOW], f"△ 목록(최대 {SHOW})": ts[:SHOW], f"필 목록(최대 {SHOW})": fs[:SHOW]})
        return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="저장된 공고를 제목으로 ○/△/× 판정(기본: 미리보기, 지우지 않음)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max", type=int, default=4000)
    ap.add_argument("--redo", action="store_true", help="이미 관련도가 있는 공고도 다시 판정")
    ap.add_argument("--email")
    ap.add_argument("--judge-model", default=tj.JUDGE_MODEL)
    ap.add_argument("--recheck", nargs="?", const="claude-sonnet-5-5", help="×로 나온 것만 이 모델(기본 claude-sonnet-5-5)이 한 번 더 확인")
    a = ap.parse_args(argv)
    print(json.dumps(run(a.apply, a.max, a.redo, a.email, a.judge_model, recheck=a.recheck), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
