from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=(settings.app_env == "development"),
    )


engine = _make_engine()

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield an AsyncSession and close it after use."""
    async with AsyncSessionFactory() as session:
        yield session


def reset_engine_for_forked_process() -> None:
    """Recreate the async engine + session factory after a ``fork()``.

    Background
    ----------
    The module-level ``engine`` is created when ``src.db.session`` is first
    imported — which, for Celery prefork workers, happens in the *parent*
    process before children are forked. The asyncpg connection pool attached
    to that engine keeps file descriptors + ``asyncio`` Future objects bound
    to the parent's event loop. Each child task then creates its own
    ``asyncio.run(...)`` loop; reusing the inherited pool raises
    ``Future ... attached to a different loop``.

    Fix
    ---
    Call this function from the ``worker_process_init`` Celery signal. It:
      1. Discards the inherited engine *without* touching its connections
         (``dispose(close=False)``) — those sockets belong to the parent.
      2. Builds a fresh engine + session factory for the child process, so
         the first task opens brand-new asyncpg connections on the child's
         own event loop.
    """
    global engine, AsyncSessionFactory

    old_engine = engine
    try:
        # close=False: do NOT attempt to close inherited sockets — they are
        # still in use by the parent. Just drop the Python-side pool state.
        old_engine.sync_engine.dispose(close=False)
    except Exception:  # pragma: no cover — defensive
        pass

    engine = _make_engine()
    AsyncSessionFactory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
