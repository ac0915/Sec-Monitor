from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Generator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import BASE_DIR, get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
_init_lock = Lock()
_initialized = False


def get_engine():
    return engine


def _build_alembic_config():
    try:
        from alembic.config import Config
    except ImportError as exc:  # pragma: no cover - environment issue
        raise RuntimeError(
            "Alembic is required to run database migrations. Install dependencies with "
            "`pip install -r requirements.txt`, or set `AUTO_RUN_MIGRATIONS=false` to "
            "boot with SQLAlchemy create_all only."
        ) from exc

    config = Config(str(Path(BASE_DIR) / "alembic.ini"))
    config.set_main_option("script_location", str(Path(BASE_DIR) / "db_migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def _run_migrations() -> None:
    if not settings.auto_run_migrations:
        Base.metadata.create_all(bind=engine, checkfirst=True)
        return

    try:
        from alembic import command
    except ImportError as exc:  # pragma: no cover - environment issue
        raise RuntimeError(
            "Alembic is required to run database migrations. Install dependencies with "
            "`pip install -r requirements.txt`, or set `AUTO_RUN_MIGRATIONS=false` to "
            "boot with SQLAlchemy create_all only."
        ) from exc

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    config = _build_alembic_config()

    if existing_tables and "alembic_version" not in existing_tables:
        Base.metadata.create_all(bind=engine, checkfirst=True)
        command.stamp(config, "head")
        return

    command.upgrade(config, "head")


def init_db() -> None:
    global _initialized
    if _initialized:
        return

    with _init_lock:
        if _initialized:
            return
        from . import models  # noqa: F401

        _run_migrations()
        _initialized = True


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
