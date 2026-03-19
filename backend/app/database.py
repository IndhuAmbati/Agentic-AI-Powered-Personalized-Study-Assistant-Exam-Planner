"""Async SQLAlchemy database engine and session factory."""

from __future__ import annotations

import asyncio

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

from backend.app.config import get_settings

settings = get_settings()
_dialect = settings.database_url.split("://", 1)[0].split("+")[0].lower()
IS_SQLITE = _dialect == "sqlite"
IS_MYSQL = _dialect in {"mysql", "mariadb"}

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    connect_args={"timeout": 60} if IS_SQLITE else {},
    poolclass=NullPool if IS_SQLITE else None,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

_db_init_lock = asyncio.Lock()
_db_initialized = False
_REQUIRED_TABLES = {
    "users",
    "subjects",
    "topics",
    "schedule_entries",
    "progress_records",
    "quizzes",
    "quiz_questions",
    "quiz_attempts",
    "auth_credentials",
    "quiz_performances",
    "chat_messages",
}


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    """Enable safer concurrent writes for SQLite."""
    if not IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=60000;")
    cursor.close()


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields an async DB session."""
    await ensure_db_ready()
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables (used at startup)."""
    global _db_initialized
    # Ensure all model classes are imported before metadata.create_all().
    # Without this, newly added tables (e.g., auth_credentials) may be missing.
    import backend.app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if IS_SQLITE:
            await _run_sqlite_compat_migrations(conn)
    _db_initialized = True


async def ensure_db_ready() -> None:
    """Lazily ensure the schema exists for request paths as well as startup."""
    global _db_initialized

    if _db_initialized and await _schema_ready():
        return

    async with _db_init_lock:
        if _db_initialized and await _schema_ready():
            return
        await init_db()
        _db_initialized = True


async def _schema_ready() -> bool:
    async with engine.begin() as conn:
        if IS_SQLITE:
            schema_result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        else:
            schema_result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
                )
            )
        tables = {row[0] for row in schema_result}
    return _REQUIRED_TABLES.issubset(tables)


async def _run_sqlite_compat_migrations(conn) -> None:
    """Best-effort schema repair for legacy SQLite files without Alembic."""
    tables_result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    )
    tables = {row[0] for row in tables_result}
    if "users" in tables:
        cols_result = await conn.execute(text("PRAGMA table_info(users)"))
        columns = {row[1] for row in cols_result}
        if "learning_preference" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN learning_preference VARCHAR(50) DEFAULT 'balanced'"
                )
            )
        if "difficulty_level" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN difficulty_level VARCHAR(20) DEFAULT 'medium'"
                )
            )
        if "updated_at" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"
                )
            )
