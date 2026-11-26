from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

from . import config


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=config.MIN_PASSWORD_LEN, max_length=200)
    invite_code: str = Field(min_length=4, max_length=32)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class InviteCreateIn(BaseModel):
    note: str | None = Field(default=None, max_length=100)
    expires_days: int | None = Field(default=7, ge=1, le=90)


class KVIn(BaseModel):
    value: Any


class FillIn(BaseModel):
    """공고 빈칸 채우기 요청. fields의 키는 gaps.WRITABLE 에 있는 칸만 받습니다."""
    fields: dict[str, str | list[str]] = Field(max_length=20)
    by: str = Field(default="pc", max_length=30, pattern=r"^[\w가-힣 ._-]+$")
    overwrite: bool = False


class ReviewRowIn(BaseModel):
    """내 PC가 읽은 면접 후기 1건(내 말로 요약한 것). 본문을 그대로 붙여 넣지 않습니다."""
    src: str = Field(max_length=30, pattern=r"^[\w가-힣 ._·-]+$")             # 캐치 · 잡플래닛 · 자소설닷컴 · 채팅방 …
    url: str = Field(max_length=500, pattern=r"^https?://")
    role: str = Field(default="", max_length=120)                              # 직무(연도)
    flow: str = Field(default="", max_length=1500)                             # 전형 흐름
    qs: str = Field(default="", max_length=2500)                               # 나온 질문
    tips: str = Field(default="", max_length=1500)                             # 준비 포인트
    n: int = Field(default=1, ge=1, le=200)                                    # 이 주소에서 읽은 후기 수


class ExampleIn(BaseModel):
    """합격 자소서 예시(링크 + 구조 요약). 본문 복사 금지."""
    t: str = Field(max_length=120)
    y: str = Field(default="", max_length=30)
    spec: str = Field(default="", max_length=300)
    pts: list[str] = Field(default_factory=list, max_length=6)
    url: str = Field(max_length=500, pattern=r"^https?://")
    src: str = Field(max_length=30)
    login: str = Field(default="", max_length=60)


class ReviewImportIn(BaseModel):
    company: str = Field(min_length=1, max_length=100)
    rows: list[ReviewRowIn] = Field(default_factory=list, max_length=12)
    examples: list[ExampleIn] = Field(default_factory=list, max_length=8)


class ListingPageIn(BaseModel):
    keyword: str = Field(default="", max_length=100)
    page: int = Field(default=1, ge=1, le=50)
    text: str = Field(default="", max_length=1_500_000)      # 사이트가 준 목록 JSON 글자 그대로


class CompactJobIn(BaseModel):
    """내 PC Chrome이 목록 JSON에서 뽑은 핵심 칸(모델을 거쳐 전달되므로 짧게). id 공고번호 · t 제목 · c 회사 · f/to 경력 시작·끝(년) · e 고용형태"""
    id: str = Field(min_length=1, max_length=20)
    t: str = Field(min_length=1, max_length=300)
    c: str = Field(default="", max_length=200)
    f: int | None = Field(default=None, ge=0, le=60)
    to: int | None = Field(default=None, ge=0, le=60)
    e: str = Field(default="", max_length=40)


class JobImportIn(BaseModel):
    source: Literal["wanted"] = "wanted"
    pages: list[ListingPageIn] = Field(default_factory=list, max_length=12)      # 목록 JSON 글자 그대로
    items: list[CompactJobIn] = Field(default_factory=list, max_length=500)      # 또는 핵심 칸만 뽑은 것
    apply: bool = True

    @model_validator(mode="after")
    def _need_some(self):
        if not self.pages and not self.items:
            raise ValueError("pages 또는 items 가 필요합니다.")
        return self
