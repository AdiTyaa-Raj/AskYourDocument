"""Database configuration and session helpers."""

from __future__ import annotations

import os
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is not None and value.strip() == "":
        return default
    return value if value is not None else default


def is_db_configured() -> bool:
    if _get_env("DATABASE_URL"):
        return True
    return all(
        _get_env(key)
        for key in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER")
    )


def get_database_url() -> str:
    url = _get_env("DATABASE_URL")
    if url:
        return url

    host = _get_env("POSTGRES_HOST", "localhost")
    port = _get_env("POSTGRES_PORT", "5432")
    name = _get_env("POSTGRES_DB", "askyourdocument")
    user = _get_env("POSTGRES_USER", "askyourdocument")
    password = _get_env("POSTGRES_PASSWORD", "askyourdocument")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url(), pool_pre_ping=True)
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def check_db() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def create_tables() -> None:
    # Import models so they are registered with SQLAlchemy metadata.
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())
