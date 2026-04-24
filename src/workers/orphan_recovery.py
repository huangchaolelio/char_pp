"""Orphan-task recovery (Feature 013).

On worker startup, any ``analysis_tasks`` row that has been in ``processing``
state for longer than ``settings.orphan_task_timeout_seconds`` (default 840s =
2 × ``task_time_limit``) is considered abandoned — typically because the
previous worker was SIGKILL'd — and is reclaimed as ``failed`` so clients can
re-submit.

Invoked synchronously from ``celery_app.py`` via the ``celeryd_after_setup``
signal (one sweep per worker boot). Failures are logged and never block worker
startup.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


async def sweep_orphan_tasks() -> int:
    """Mark stale ``processing`` rows as ``failed``; return the number reclaimed."""
    from src.config import get_settings
    from src.models.analysis_task import AnalysisTask, TaskStatus

    settings = get_settings()
    timeout_s = settings.orphan_task_timeout_seconds
    cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=timeout_s)

    engine = create_async_engine(
        settings.database_url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            stmt = (
                update(AnalysisTask)
                .where(
                    AnalysisTask.status == TaskStatus.processing,
                    AnalysisTask.started_at.isnot(None),
                    AnalysisTask.started_at < cutoff,
                )
                .values(
                    status=TaskStatus.failed,
                    completed_at=datetime.now(tz=timezone.utc),
                    error_message="orphan recovered on worker restart",
                )
                .returning(AnalysisTask.id)
            )
            result = await session.execute(stmt)
            reclaimed_ids = result.fetchall()
            await session.commit()
            count = len(reclaimed_ids)
            if count:
                logger.warning(
                    "orphan recovery: reclaimed %d stale 'processing' task(s) "
                    "older than %ds",
                    count,
                    timeout_s,
                )
            else:
                logger.info("orphan recovery: no stale tasks found (> %ds)", timeout_s)
            return count
    finally:
        await engine.dispose()


def sweep_orphan_tasks_sync() -> int:
    """Blocking wrapper for use in Celery signal handlers."""
    return asyncio.run(sweep_orphan_tasks())
