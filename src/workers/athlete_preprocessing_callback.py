"""Feature-020 · Celery callback: 运动员预处理完成后回写素材表.

由 :func:`AthleteSubmissionService._dispatch_preprocessing_chain` 通过
``preprocess_video.si(job_id) | mark_athlete_preprocessed_cb.si(job_id, key)``
链式调用。预处理成功后写 ``athlete_video_classifications.preprocessed=true``
+ ``preprocessing_job_id=<job_id>``；若 preprocess_video 失败则 chain 自动短路。
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


def _make_session_factory():
    from src.config import get_settings

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False,
    )


async def _run_mark(job_id: str, cos_object_key: str) -> dict:
    from src.services import preprocessing_service as _ps

    factory = _make_session_factory()
    async with factory() as session:
        await _ps.mark_athlete_preprocessed(
            session,
            cos_object_key=cos_object_key,
            preprocessing_job_id=UUID(job_id),
        )
        await session.commit()
    return {"job_id": job_id, "cos_object_key": cos_object_key, "marked": True}


@shared_task(
    bind=True,
    name="src.workers.athlete_preprocessing_callback.mark_athlete_preprocessed_cb",
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
)
def mark_athlete_preprocessed_cb(self, job_id: str, cos_object_key: str) -> dict:
    """Mark athlete_video_classifications.preprocessed = true after success."""
    logger.info(
        "mark_athlete_preprocessed_cb: job_id=%s cos_key=%s", job_id, cos_object_key,
    )
    try:
        return asyncio.run(_run_mark(job_id, cos_object_key))
    except Exception as exc:
        logger.exception(
            "mark_athlete_preprocessed_cb failed: job_id=%s error=%s",
            job_id, exc,
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {
                "job_id": job_id,
                "cos_object_key": cos_object_key,
                "marked": False,
                "error": str(exc),
            }
