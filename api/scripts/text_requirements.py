"""텍스트 공고 본문에서 '필수(자격요건)'와 '우대'를 규칙으로 나눕니다(AI 비용 0).
구역 제목(자격요건·필수·우대 등)을 찾아 다음 구역 제목 전까지를 가져옵니다. 확실하지 않으면 비워 둡니다(= 빈칸으로 남아 나중에 채움).
- '주요업무 / 자격요건'처럼 업무와 자격이 한 제목에 섞인 구역은 나눌 수 없어 건너뜁니다.
- 한 구역은 최대 10,000자, 항목 앞의 점·번호·기호는 지웁니다. 전체 원문은 별도로 보존합니다.
"""
import re

REQ = re.compile(r"^(자격\s*요건|필수\s*(?:자격|요건|사항|조건)?|지원\s*자격|응시\s*자격|자격\s*조건|자격\s*사항)")
PREF = re.compile(r"^(우대\s*(?:사항|조건|요건)?|가산점|선호\s*(?:자격|요건)?)")
WORK = re.compile(r"^(주요\s*업무|담당\s*업무|하는\s*일|업무\s*내용|수행\s*업무|직무\s*(?:소개|내용))")
BENEFIT = re.compile(r"^(복리\s*후생|복지|혜택)")
PROCESS = re.compile(r"^(채용\s*(?:절차|프로세스|전형)|전형\s*절차)")
OTHER = re.compile(r"^(근무\s*(?:조건|지|환경|형태|시간)|복지|혜택|채용\s*(?:절차|프로세스|전형|조건)|전형\s*절차|접수|제출|급여|연봉|회사\s*소개|기업\s*소개|모집\s*(?:분야|부문|요강|인원)|지원\s*방법|유의\s*사항|기타|근로\s*조건|고용\s*형태|직급|공통\s*사항|문의)")
LEAD = re.compile(r"^[\s\[\(【<■□◆◇●○▶▷★☆※✔✅📋🏠🎁🚀🛎💼🎯📌\-\*•·ㆍ]+")
TAIL = re.compile(r"[\s\]\)】>:：]+$")
BULLET = re.compile(r"^\s*(?:[-•·ㆍ●○■□◆◇▶▷※\*]|\d{1,2}[\.\)]|[①-⑩])\s*")
MAX_SECTION = 10000


def _heading(line: str):
    """줄이 구역 제목이면 (종류, 같은 줄에 붙은 내용). 아니면 None. 제목은 짧아야 함(긴 문장은 제목이 아님)."""
    t = line.strip()
    core = TAIL.sub("", LEAD.sub("", t))
    m = re.match(r"^(.{1,25}?)\s*[:：]\s*(.+)$", LEAD.sub("", t))
    head, rest = (m.group(1), m.group(2)) if m else (core, "")
    head = TAIL.sub("", head).strip()
    if not head or len(head) > 25:
        return None
    if re.search(r"업무", head) and re.search(r"자격|우대|요건", head):
        return ("mixed", "")                      # '주요업무 / 자격요건' 같은 섞인 제목: 나눌 수 없음
    for kind, rx in (("req", REQ), ("pref", PREF), ("work", WORK), ("benefit", BENEFIT), ("process", PROCESS), ("other", OTHER)):
        if rx.match(head):
            return (kind, rest.strip())
    return None


def _clean(lines: list[str]) -> str:
    out, seen = [], set()
    for ln in lines:
        s = BULLET.sub("", ln).strip()
        if len(s) < 2 or s in seen:
            continue
        seen.add(s)
        out.append(f"- {s}")
    return "\n".join(out)[:MAX_SECTION]


def parse_sections(text: str) -> dict:
    """원문 구역을 업무·필수·우대·복지·전형으로 나눈다. 원문은 별도로 보존한다."""
    sections: dict[str, list[str]] = {}
    cur = None
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        h = _heading(line)
        if h:
            cur = h[0]
            if cur in ("req", "pref", "work", "benefit", "process") and h[1]:
                sections.setdefault(cur, []).append(h[1])
            continue
        if cur in ("req", "pref", "work", "benefit", "process"):
            sections.setdefault(cur, []).append(line)
    out = {}
    for kind, key in (("work", "담당 업무"), ("req", "필수"), ("pref", "우대"),
                      ("benefit", "복지"), ("process", "전형 절차")):
        c = _clean(sections.get(kind, []))
        if c:
            out[key] = c
    return out


def parse_requirements(text: str) -> dict:
    """기존 호출부용: 필수·우대만 돌려준다."""
    return {k: v for k, v in parse_sections(text).items() if k in ("필수", "우대")}
