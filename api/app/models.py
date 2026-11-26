from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, JSONType


def utcnow() -> datetime:
    """모든 시각은 UTC naive로 통일해 저장합니다(SQLite/Postgres 동작 차이 방지)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Session(Base):
    """서버 쪽 세션. 쿠키에는 무작위 토큰만 있고, DB에는 그 해시만 저장합니다."""
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class InviteCode(Base):
    __tablename__ = "invite_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Job(Base):
    """공용 데이터: 모든 로그인 사용자가 같은 공고를 봅니다. raw에 보드의 원본 항목을 그대로 보관합니다."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)  # 원문 URL(중복 방지 키)
    company: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    deadline_text: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 원문 그대로
    deadline_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)  # 날짜만
    deadline_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "23:59", "24:00"
    last_checked: Mapped[str | None] = mapped_column(String(30), nullable=True)
    posted_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)  # 공고가 사이트에 등록된 날
    first_seen: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)  # 보드가 처음 수집한 날
    raw: Mapped[dict] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SharedData(Base):
    """공용 데이터 덩어리(기업정보, 면접 후기, 잡플래닛 등)를 key별 JSON으로 보관."""
    __tablename__ = "shared_data"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UserKV(Base):
    """개인 데이터: 내 정보, 공고별 준비 등. 항상 (user_id, key)로만 접근합니다."""
    __tablename__ = "user_kv"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Document(Base):
    """보드가 쓰는 문서 저장소(컬렉션/문서 id). 컬렉션 경로로 공용·개인을 구분하고 접근 규칙은 라우터에서 검사합니다."""
    __tablename__ = "documents"
    collection: Mapped[str] = mapped_column(String(200), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class StoredFile(Base):
    __tablename__ = "stored_files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserApiKey(Base):
    """(옛 구조) 사용자가 등록한 Claude API 키. 새 키는 user_ai_keys에 저장하고, 이 표는 처음 읽을 때 옮겨 담는 용도로만 남깁니다. 평문은 저장하지 않고 암호문과 끝 4자리(표시용)만 보관합니다."""
    __tablename__ = "user_api_keys"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UserAiKey(Base):
    """사용자가 등록한 AI 서비스 키(ChatGPT·Claude·Gemini). 서비스별 1행. 평문은 저장하지 않고 암호문과 끝 4자리만 보관합니다."""
    __tablename__ = "user_ai_keys"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), primary_key=True)  # openai | anthropic | gemini
    ciphertext: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8))
    model_default: Mapped[str] = mapped_column(String(100), default="")  # 기본 작업에 쓸 모델
    model_quick: Mapped[str] = mapped_column(String(100), default="")    # 가벼운 작업에 쓸 모델(비면 기본 모델)
    active: Mapped[bool] = mapped_column(Boolean, default=False)         # 계정당 하나만 True
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AiUsage(Base):
    """계정별·날짜별 AI 호출 수와 토큰 수(사용량 제한·확인용). 질문·답변 내용은 저장하지 않습니다."""
    __tablename__ = "ai_usage"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)


class CollectRun(Base):
    """수집기 실행 기록(미리보기 포함). 한 번 돌 때마다 한 줄: 몇 건 읽고·저장하고·상세 조회했는지, AI 모델·설정·토큰·비용.
    공고 내용·AI 답변은 넣지 않고 숫자와 설정만 남깁니다."""
    __tablename__ = "collect_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10), index=True)               # 실행한 날(한국 시간) YYYY-MM-DD
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)  # UTC
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mode: Mapped[str] = mapped_column(String(20))                          # 저장 / 미리보기
    status: Mapped[str] = mapped_column(String(20), default="실행 중")       # 완료 / 차단으로 중단 / 오류
    keywords: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)              # 사이트에 보낸 요청 수(목록+상세)
    list_pages: Mapped[int] = mapped_column(Integer, default=0)
    listed: Mapped[int] = mapped_column(Integer, default=0)                # 목록에서 본 공고 줄 수(중복 포함)
    known: Mapped[int] = mapped_column(Integer, default=0)                 # 이미 보드에 있던 것
    new_found: Mapped[int] = mapped_column(Integer, default=0)             # 새 공고(중복 뺀)
    skipped: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)   # 조건(경력·학력·제외 단어)으로 저장하지 않은 공고
    saved_new: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    detail_requests: Mapped[int] = mapped_column(Integer, default=0)
    detail_ok: Mapped[int] = mapped_column(Integer, default=0)
    image_postings: Mapped[int] = mapped_column(Integer, default=0)        # 본문이 이미지뿐인 공고
    ai_reads: Mapped[int] = mapped_column(Integer, default=0)
    ai_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ai_settings: Mapped[dict | None] = mapped_column(JSONType, nullable=True)   # thinking·effort·조각 크기·max_tokens 등
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    blocked: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    per_keyword: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    options: Mapped[dict | None] = mapped_column(JSONType, nullable=True)       # 실행 옵션(--details, --ai-images, stop_known 등)


class DemoUser(Base):
    """가입 없이 둘러보는 임시 계정. 만료되면 계정과 데이터가 함께 지워집니다."""
    __tablename__ = "demo_users"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
