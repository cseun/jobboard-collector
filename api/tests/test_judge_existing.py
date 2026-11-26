import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job
from scripts import judge_existing as je


def ask(prompt):
    rows = []
    for ln in prompt.split("[공고 목록: 번호 / 회사 / 제목]\n")[1].splitlines():
        i, _c, t = ln.split("\t")
        rows.append({"i": int(i), "v": "×" if "디자이너" in t else "○", "w": "테스트"})
    return {"text": json.dumps({"r": rows}, ensure_ascii=False), "in": 100, "out": 50}


def test_preview_apply_and_never_deletes(client):
    with SessionLocal() as db:
        for i, t in enumerate(["로봇 SW 개발", "UX 디자이너", "PLC 제어"]):
            db.add(Job(source_key=f"https://x/{i}", company=f"C{i}", title=t, raw={}))
        db.commit()
    pre = je.run(False, ask=ask)
    assert pre["판정한 공고"] == 3 and (pre["○"], pre["×"]) == (2, 1) and "UX 디자이너" in pre["× 목록(최대 80)"][0]
    with SessionLocal() as db:
        assert all("관련도" not in (j.raw or {}) for j in db.execute(select(Job)).scalars())
    je.run(True, ask=ask)
    with SessionLocal() as db:
        jobs = db.execute(select(Job).order_by(Job.id)).scalars().all()
    assert [j.raw["관련도"] for j in jobs] == ["○", "×", "○"] and jobs[1].raw["관련도 이유"] == "테스트" and len(jobs) == 3     # 지우지 않음
    assert je.run(False, ask=ask)["판정한 공고"] == 0                      # 이미 판정한 건 건너뜀
    assert je.run(False, redo=True, ask=ask)["판정한 공고"] == 3


def test_failed_batch_not_recorded(client):
    with SessionLocal() as db:
        db.add(Job(source_key="https://x/1", company="C", title="로봇", raw={}))
        db.commit()
    out = je.run(True, ask=lambda p: {"text": "모름", "in": 1, "out": 1})
    assert out["실패한 묶음"] == 1 and out["△"] == 1
    with SessionLocal() as db:
        assert "관련도" not in db.execute(select(Job)).scalars().first().raw        # 다음에 다시 시도


def test_recheck_overrides_x_only(client):
    from scripts import judge_existing as je
    from tests.test_title_judge import ask_by_title
    with SessionLocal() as db:
        for i, t in enumerate(["UX 디자이너", "LLM 엔지니어 금융", "로봇 SW"]):
            db.add(Job(source_key=f"https://x/r{i}", company=f"C{i}", title=t, raw={"구분": "[신규·수집]"}))
        db.commit()
    first = ask_by_title({"디자이너": "×", "LLM": "×"}, default="○")
    second = ask_by_title({"디자이너": "×"}, default="○")
    r = je.run(True, ask=first, recheck="claude-sonnet-5-5", recheck_ask=second)
    assert r["×"] == 1 and r["× 재확인"]["다시 본 ×"] == 2 and r["× 재확인"]["×가 아니게 됨"] == 1
    with SessionLocal() as db:
        got = {j.title: (j.raw or {}).get("관련도") for j in db.execute(select(Job)).scalars()}
    assert got["UX 디자이너"] == "×" and got["LLM 엔지니어 금융"] == "○"
