"""
SQLAlchemy async engine setup.

Supports SQLite (default) and can be pointed at PostgreSQL via DB_URL.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None


def _make_engine(db_url: str) -> AsyncEngine:
    # Convert sync SQLite URL to async driver
    if db_url.startswith("sqlite:///"):
        path = db_url.removeprefix("sqlite:///")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        async_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    elif db_url.startswith("postgresql://"):
        async_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        async_url = db_url

    kwargs: dict = {}
    if "sqlite" in async_url:
        # aiosqlite uses NullPool — pool_size and connect_args are not supported
        from sqlalchemy.pool import NullPool
        kwargs["poolclass"] = NullPool

    engine = create_async_engine(async_url, echo=False, **kwargs)
    logger.info("Database engine created: %s", async_url.split("?")[0])
    return engine


async def init_db(db_url: str) -> None:
    """Create engine, session factory, and all tables."""
    global _engine, _session_factory

    _engine = _make_engine(db_url)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    from app.storage.schema import Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")


def get_session_factory() -> async_sessionmaker:
    if _session_factory is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _session_factory


async def get_session() -> AsyncSession:
    factory = get_session_factory()
    return factory()
