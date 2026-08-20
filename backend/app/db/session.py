"""
Async SQLAlchemy engine + session factory.

Uses connection pooling with sensible defaults for Supabase free tier.
The engine is created lazily on first use and shared across the process.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine = None
_session_factory = None


def _normalize_asyncpg_url(url: str) -> str:
    """Force the asyncpg driver in the DB URL scheme.

    Render's managed Postgres `fromDatabase` connection string is a plain
    `postgresql://...` URI, which SQLAlchemy resolves to the sync psycopg2
    dialect by default — not installed here, since this project uses
    asyncpg for the async engine. Supabase URLs are usually already
    `postgresql://` too. Rewrite any bare `postgresql://` /
    `postgres://` scheme to `postgresql+asyncpg://`; leave an already
    explicit `+asyncpg` (or other driver) scheme untouched.
    """
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def get_engine():
    """Lazy singleton engine — created once per process."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            _normalize_asyncpg_url(settings.database_url),
            echo=settings.debug,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # detect stale Supabase connections
            pool_recycle=300,  # recycle connections every 5 min
        )
    return _engine


def get_session_factory():
    """Lazy singleton session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session, auto-closes on exit."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()
