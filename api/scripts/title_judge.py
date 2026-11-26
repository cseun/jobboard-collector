"""공고 제목으로 '내 직무와 관련 있나'를 가벼운 모델(Haiku)에게 ○ / △ / × 로 물어봅니다.

- 저장하기 전에 한 번 거르는 용도입니다. ×만 저장하지 않고, ○·△는 저장합니다(애매하면 △ = 남김).
- 모델·AI가 실패하거나 JSON으로 답하지 않으면 그 묶음은 전부 △(남김)로 둡니다. 판정 때문에 공고를 잃지 않게 하려는 규칙입니다.
- 판정 근거는 제목·회사 이름뿐입니다(본문은 읽지 않음).
"""
from __future__ import annotations

JUDGE_MODEL = "claude-haiku-5-5"
BATCH = 30            # 30건씩: 답이 짧아 잘릴 일이 적고, 한 묶음이 실패해도 피해가 작음
MAX_TOKENS = 8000

PROFILE = """\
나는 로봇·AX(AI 전환) 분야 신입~경력 5년 이하 SW 개발자 취업을 준비하는 사람이다. 아래 공고 제목이 내 구직 대상인지 판정해 줘.

[내 구직 대상 직무 — ○]
로봇 SW(제어·ROS·자율주행), 임베디드·펌웨어, PLC·자동화 제어, 장비 제어 SW(PC제어·반도체 장비 SW), 비전·영상처리 SW,
SW 테스트·검증(QA), AI·LLM·에이전트·RAG·생성형 AI·AX 엔지니어(도메인 무관), MES·스마트팩토리 SW
(현장 필드엔지니어·기술지원·CS는 ○가 아니라 아래 '필')

[판정]
○ = 위 직무와 직접 관련
필 = 필드엔지니어 성격(출장·현장 중심): 필드엔지니어·FAE·필드서비스·현장 설치·시운전·티칭·셋업·유지보수·운용 엔지니어·고객 현장 기술지원/CS·FDE(설치·출장 중심). 지우지 않고 '필드엔지니어'로 묶어 따로 본다. 수리·A/S·정비만이 중심이면 ×.
△ = 애매함: 직무가 제목만으로 불분명(예: "부문별 인재채용"), 일부만 관련
× = 확실히 관련 없음: 디자인·기획·마케팅·영업·인사·총무·회계·경영지원, 생산·조립·오퍼레이터·물류 현장직, 기구·기계·전기·회로·반도체 공정 설계(SW가 아님),
    교육·강사, 의료·요식·서비스직, 수리·A/S·부품 교체·정비 중심 현장직(필드엔지니어 성격이라도 수리·정비가 중심이면 ×),
    로봇·제조·임베디드·비전과 무관한 서비스용 머신러닝 엔지니어(추천·예측·커머스·핀테크·음성/언어 모델 등 ML 모델 개발),
    기술영업, 기획자·PM(프로덕트·프로젝트·서비스 기획 포함), 팀장·파트장·리드·책임·수석·임원급(관리·리더 직책),
    일반 웹·앱 서비스 개발(프론트엔드·백엔드·풀스택·웹·모바일 앱)

[예외 — 이런 건 ×로 하지 않는다]
- 현장 직무(필드엔지니어·설치·시운전·티칭·셋업·기술지원·CS)는 '필'이다. 단 제목·요약에서 PLC·제어 프로그래밍·SW 개발·비전 알고리즘이 **주업무**로 명확하면 '필'이 아니라 △ (현장 비중이 크면 '필').
- ERP·MES·POP·QMS·WMS 같은 생산·제조 업무시스템 개발(C#/.NET 포함)은 ○ 또는 △ — 일반 웹/앱 서비스 개발로 보지 않는다.
- ML엔지니어라도 LLM·NLP·에이전트·RAG가 제목에 있으면 ○ (제목에 추천·예측 모델 개발만 있는 순수 ML만 ×).
- 로봇·제조·자동화 회사의 웹 백엔드·프론트엔드, 안드로이드 시스템(SW), 임베디드(H/W) 개발은 △ (일반 서비스 회사의 웹/앱만 ×).
- ×는 위 '×' 목록의 **직무 유형**일 때만 준다. 직무는 맞는데 산업(의료·금융·커머스 등)이 낯설거나 '로봇·제조 맥락이 안 보인다'는 이유만으로는 절대 ×가 아니다 → △(AI·LLM·에이전트는 ○).
- SW QA·QC·테스트·검증 엔지니어는 도메인과 상관없이 ○ 또는 △ (제목이 길면 본문 속 단어로 도메인을 추측하지 말 것).
- **머신비전·광학(렌즈·조명·카메라) 쪽** 광학·기구 설계만 △. 로봇·자동화·AMR의 기구설계·기계설계·감속기·액추에이터·모터 HW·전장·회로 설계는 분야가 로봇이어도 ×(SW가 아님).
- FAE(Field Application Engineer)·필드서비스·고객 현장 기술지원은 펌웨어·MCU 기술지원이라도 '필'(현장 직무). FDE(Forward Deployed Engineer)는 SW 개발이 주업무로 보이면 △, 현장 설치·출장 중심이면 '필'.
- MES·AX·스마트팩토리·로봇·Physical AI 분야의 PM·컨설턴트·사업관리는 △ (분야 없는 순수 프로젝트/프로덕트 매니저만 ×).
- 제목에 SW·펌웨어·제어·PLC·임베디드·ROS 같은 말이 같이 있으면(예: "회로설계 및 Firmware 개발") ○ 또는 △.
- LLM·에이전트·RAG·생성형 AI·프롬프트·AX 엔지니어/개발자는 도메인(금융·의료·커머스 등)과 상관없이 ○. (일반 ML 모델 개발만 ×)
- 머신러닝/ML/AI 엔지니어라도 로봇·Physical AI·제조·임베디드·온디바이스·비전·자율주행 맥락이 있으면 ○ 또는 △.
- "생산"이어도 생산관리 시스템(MES)·스마트팩토리 SW면 ○.
- "설계"만으로 ×가 아니다: SW·시스템·제어 설계는 ○.
- 웹·앱이어도 MES·스마트팩토리·로봇 관제(ACS/FMS)·장비 제어 UI·AI 서비스를 만드는 SW면 △ 이상(일반 서비스 웹/앱만 ×).
- 직책 단어(리드·책임 등)는 관리·리더 직책일 때만 ×. 팀원 모집인데 "팀 리드와 협업"처럼 쓰인 경우는 ×가 아니다.

[규칙]
- 확신이 없으면 ×가 아니라 △로 한다.
- 이유(w)는 15자 안팎으로 짧게.
- 반드시 JSON만 답한다: {"r":[{"i":번호,"v":"○|△|×|필","w":"이유"}, ...]}  (입력의 모든 번호에 대해)
"""

_MAP = {"○": "○", "o": "○", "O": "○", "◯": "○", "△": "△", "▲": "△", "?": "△", "×": "×", "x": "×", "X": "×", "✕": "×", "필": "필", "F": "필", "f": "필"}


def build_prompt(batch: list[dict]) -> str:
    lines = "\n".join(f"{i}\t{(p.get('company') or '')[:30]}\t{(p.get('title') or '')[:90]}" for i, p in enumerate(batch))
    return f"{PROFILE}\n[공고 목록: 번호 / 회사 / 제목]\n{lines}\n"


def _make_ask(key: str, model: str):
    """Haiku 호출. 생각(thinking)이 출력 토큰을 다 써서 답이 잘리는 걸 막으려고 끄고 호출 → 서비스가 거부하면 설정 없이 다시."""
    from app.routers import ai as ai_mod

    def ask(prompt: str) -> dict:
        msgs = [{"role": "user", "text": prompt}]
        try:
            return ai_mod._call("anthropic", key, model, msgs, [], MAX_TOKENS, {"thinking": {"type": "disabled"}})
        except Exception:
            return ai_mod._call("anthropic", key, model, msgs, [], MAX_TOKENS)
    return ask


FIELD_ROLE = "필드엔지니어"


def tag_relevance(raw: dict, v: str) -> dict:
    """판정 결과를 공고 raw에 기록. '필'이면 직무 분류를 ['필드엔지니어']로 묶고(원래 값은 '직무 분류 원본'에 보관), 아니면 원래대로 되돌림."""
    raw["관련도"] = v
    if v == "필":
        if "직무 분류 원본" not in raw:
            raw["직무 분류 원본"] = raw.get("직무 분류") or []
        raw["직무 분류"] = [FIELD_ROLE]
    elif "직무 분류 원본" in raw:
        orig = raw.pop("직무 분류 원본")
        raw["직무 분류"] = orig or ["기타 SW"]
    return raw


def _verdict(v) -> str:
    return _MAP.get(str(v or "").strip()[:1], "△")


def judge(items: list[dict], key: str, model: str = JUDGE_MODEL, ask=None, batch: int = BATCH) -> dict:
    """items 각각에 대해 {"v": ○/△/×, "w": 이유} 를 돌려줍니다. 반환: {"result": [..items와 같은 순서..], "tokens_in", "tokens_out", "cost_usd", "failed": 실패한 묶음 수}"""
    from app.routers import ai as ai_mod
    from scripts.test_image_read import estimate_cost

    ask = ask or _make_ask(key, model)
    result: list[dict] = [{"v": "△", "w": "판정 안 함"} for _ in items]
    tin = tout = failed = 0
    why = ""
    for s in range(0, len(items), batch):
        chunk = items[s:s + batch]
        try:
            res = ask(build_prompt(chunk))
            if res.get("truncated"):
                why = "답이 길어서 잘림(max_tokens)"
            tin += res.get("in") or 0
            tout += res.get("out") or 0
            data = ai_mod._parse_json(res.get("text") or "") or {}
            rows = data.get("r") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                failed += 1
                why = why or "JSON 아님: " + (res.get("text") or "(빈 답)")[:120].replace("\n", " ")
                continue
            for r in rows:
                if isinstance(r, dict) and isinstance(r.get("i"), int) and 0 <= r["i"] < len(chunk):
                    result[s + r["i"]] = {"v": _verdict(r.get("v")), "w": str(r.get("w") or "")[:40]}
        except Exception as e:   # 한 묶음이 실패해도 나머지는 계속. 실패한 묶음은 △(남김).
            failed += 1
            why = why or f"오류: {type(e).__name__}: {str(e)[:120]}"
    cost = estimate_cost("anthropic", model, tin, tout) or 0.0
    out = {"result": result, "tokens_in": tin, "tokens_out": tout, "cost_usd": cost, "failed": failed, "model": model}
    if failed and why:
        out["실패 이유"] = why
    return out


# ───────────────────────── 직급 확인(책임·팀장급과 팀원을 같이 뽑는 공고) ─────────────────────────
SENIOR_PROMPT = """\
아래 채용공고들은 제목에 팀장·책임·수석·리드 같은 직급 말이 있다. 각 공고의 본문을 읽고, 신입~경력 5년 이하 팀원/사원급도 지원 대상인지 판정해 줘.

- junior=true : 팀원·사원·주임·대리·신입·주니어를 함께 뽑거나, 요구 경력이 5년 이하로 열려 있음
- junior=false : 팀장·책임·수석·리더급(경력 5년 초과, 관리·총괄 직책)만 뽑음
- 본문만으로 알 수 없으면 junior=true (모르면 남긴다)
- 이유(w)는 20자 안팎.
반드시 JSON만 답한다: {"r":[{"i":번호,"junior":true|false,"w":"이유"}, ...]}
"""
SENIOR_BATCH = 6
SENIOR_TEXT = 1800


def judge_senior(items: list[dict], key: str, model: str = JUDGE_MODEL, ask=None) -> dict:
    """items: [{company,title,text}] → {"result":[{"junior":bool,"w":str}...], tokens_in, tokens_out, cost_usd, failed}. 실패하면 junior=True(남김)."""
    from app.routers import ai as ai_mod
    from scripts.test_image_read import estimate_cost

    ask = ask or _make_ask(key, model)
    result = [{"junior": True, "w": "판정 안 함"} for _ in items]
    tin = tout = failed = 0
    for s in range(0, len(items), SENIOR_BATCH):
        chunk = items[s:s + SENIOR_BATCH]
        body = "\n\n".join(f"[{i}] 회사: {c.get('company', '')} / 제목: {c.get('title', '')}\n본문: {(c.get('text') or '')[:SENIOR_TEXT]}" for i, c in enumerate(chunk))
        try:
            res = ask(f"{SENIOR_PROMPT}\n{body}\n")
            tin += res.get("in") or 0
            tout += res.get("out") or 0
            data = ai_mod._parse_json(res.get("text") or "") or {}
            rows = data.get("r") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                failed += 1
                continue
            for r in rows:
                if isinstance(r, dict) and isinstance(r.get("i"), int) and 0 <= r["i"] < len(chunk) and isinstance(r.get("junior"), bool):
                    result[s + r["i"]] = {"junior": r["junior"], "w": str(r.get("w") or "")[:40]}
        except Exception:
            failed += 1
    return {"result": result, "tokens_in": tin, "tokens_out": tout, "cost_usd": estimate_cost("anthropic", model, tin, tout) or 0.0, "failed": failed, "model": model}


# ───────────────────────── Jev (TypeSafe System One) — 비교 시험용 ─────────────────────────
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_BATCH = 20
JEV_PRICE_IN = 0.042          # USD / 100만 입력 토큰(출력은 무료) — 2026-11 공개 가격
JEV_CHOICES = {"O": "내 구직 대상 직무와 직접 관련(로봇 SW·임베디드·PLC·장비제어·비전·QA·AI/LLM/AX·MES/스마트팩토리 등)",
               "M": "애매함: 제목만으로 직무가 불분명하거나 일부만 관련",
               "X": "확실히 관련 없음(디자인·기획·마케팅·영업·경영지원·생산직·기구/전기 설계·교육·서비스용 ML·일반 웹/앱·팀장급 등)"}
_JEV_MAP = {"O": "○", "M": "△", "X": "×"}


def judge_jev(items: list[dict], api_key: str, model: str = JEV_MODEL, post=None, batch: int = JEV_BATCH, workers: int = 4) -> dict:
    """Jev 한 번 호출에 공고 batch건을 상태 글로 주고 건마다 choice 질문 1개씩(O/M/X)을 보냄. 반환 모양은 judge()와 같음(+ conf 목록).
    이유 글은 만들지 못하므로 w에는 확신도를 적음. 실패한 묶음은 △."""
    import httpx
    from concurrent.futures import ThreadPoolExecutor

    def default_post(body: dict) -> dict:
        r = httpx.post(JEV_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
        r.raise_for_status()
        return r.json()

    post = post or default_post
    chunks = [(s, items[s:s + batch]) for s in range(0, len(items), batch)]
    result: list[dict] = [{"v": "△", "w": "판정 안 함", "conf": None} for _ in items]
    use = {"in": 0, "failed": 0}

    def work(arg):
        s, chunk = arg
        state = PROFILE.split("[판정]")[0] + "\n[공고 목록: 번호 / 회사 / 제목]\n" + "\n".join(
            f"{i}\t{(c.get('company') or '')[:30]}\t{(c.get('title') or '')[:90]}" for i, c in enumerate(chunk))
        body = {"state": state, "model": model, "questions": {
            f"q{i}": {"type": "choice", "instructions": f"위 목록의 {i}번 공고는 내 구직 대상인가?", "criteria": JEV_CHOICES} for i in range(len(chunk))}}
        try:
            data = post(body)
            use["in"] += (data.get("usage") or {}).get("input_tokens", 0)
            for i in range(len(chunk)):
                a = (data.get("answers") or {}).get(f"q{i}") or {}
                if a.get("choice") in _JEV_MAP:
                    result[s + i] = {"v": _JEV_MAP[a["choice"]], "w": f"확신 {float(a.get('confidence') or 0):.2f}", "conf": a.get("confidence")}
        except Exception:
            use["failed"] += 1

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, chunks))
    return {"result": result, "tokens_in": use["in"], "tokens_out": 0, "cost_usd": use["in"] * JEV_PRICE_IN / 1e6, "failed": use["failed"], "model": model}
