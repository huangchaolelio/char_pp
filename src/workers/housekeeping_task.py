"""Celery housekeeping tasks — data retention cleanup (Feature 013).

Routed to the ``default`` queue and triggered by Celery beat daily.
Migrated from the legacy ``src.workers.athlete_video_task.cleanup_expired_tasks``;
the old module is deleted as part of T017.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings

logger = logging.getLogger(__name__)


def _make_session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


@shared_task(
    name="src.workers.housekeeping_task.cleanup_expired_tasks",
    bind=False,
)
def cleanup_expired_tasks() -> dict:
    """Daily cleanup: physically delete expired / soft-deleted analysis tasks.

    Removes tasks where:
      - ``deleted_at IS NOT NULL`` (user explicitly deleted), OR
      - ``completed_at < NOW() - data_retention_months``

    Cascade deletes all associated data (motion analyses, deviations,
    coaching advice, expert tech points, audio transcripts, …).
    """

    async def _run_cleanup() -> int:
        settings = get_settings()
        retention_months = settings.data_retention_months
        cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=retention_months * 30)
        factory = _make_session_factory()

        async with factory() as session:
            async with session.begin():
                from src.models.analysis_task import AnalysisTask as AT

                stmt = (
                    delete(AT)
                    .where(
                        or_(
                            AT.deleted_at.isnot(None),
                            AT.completed_at < cutoff_date,
                        )
                    )
                    .returning(AT.id)
                )
                result = await session.execute(stmt)
                return len(result.fetchall())

    count = asyncio.run(_run_cleanup())
    logger.info("Data retention cleanup: physically deleted %d expired tasks", count)
    return {"deleted_count": count}
