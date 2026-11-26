from sqlalchemy import JSON, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from . import config

# Postgres에서는 JSONB, 테스트용 SQLite에서는 일반 JSON
JSONType = JSON().with_variant(JSONB(), "postgresql")

_kwargs: dict = {"pool_pre_ping": True}
if config.DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in config.DATABASE_URL or config.DATABASE_URL.endswith("sqlite://"):
        _kwargs["poolclass"] = StaticPool

engine = create_engine(config.DATABASE_URL, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """표를 만들고, 이미 있는 표에 새로 생긴 칸(비어도 되는 칸)을 덧붙입니다. 여러 번 불러도 안전합니다.
    (create_all은 새 표만 만들고 기존 표에 칸을 추가하지 않기 때문에 필요합니다.)"""
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or col.primary_key or not col.nullable:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
                if col.index:
                    conn.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_{table.name}_{col.name}" ON "{table.name}" ("{col.name}")'))
