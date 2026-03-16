"""Database configuration and session helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import create_engine, inspect, text
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


def bootstrap_schema() -> None:
    """Ensure the database schema exists.

    Prefers Alembic migrations when available; falls back to SQLAlchemy
    `create_all()` for lightweight/dev usage.
    """

    root_dir = Path(__file__).resolve().parents[3]
    alembic_ini = root_dir / "alembic.ini"
    if not alembic_ini.exists():
        create_tables()
        return

    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        create_tables()
        return

    cfg = Config(str(alembic_ini))
    engine = get_engine()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    managed_tables = {
        "tenants",
        "users",
        "roles",
        "permissions",
        "user_roles",
        "role_permissions",
    }

    if "alembic_version" not in existing_tables and (existing_tables & managed_tables):
        # Backward-compat: earlier versions of the app created tables via create_all().
        # For an already-bootstrapped database, ensure any missing tables exist and stamp
        # the current migration head so future upgrades work.
        create_tables()
        command.stamp(cfg, "head")
        return

    command.upgrade(cfg, "head")
