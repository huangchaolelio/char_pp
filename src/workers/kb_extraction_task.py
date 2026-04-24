"""Celery task: knowledge-base extraction for a classified coach video (Feature 013).

Routed to the ``kb_extraction`` queue (capacity 50, concurrency 2).

Flow:
  1. Mark ``analysis_tasks`` row as ``processing`` with ``started_at=now()``.
  2. Delegate to ``KbExtractionService.extract_knowledge`` (wired in US3/T038/T039).
  3. On completion, mark ``success`` and store the resulting KB version (when applicable).
  4. On failure, mark ``failed`` and surface the truncated error message.

The heavy lifting (video download, audio transcription, LLM tip extraction) lives
in the service layer; the Celery task here is intentionally thin.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from celery import shared_task
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


def _make_session_factory():
    """Create a fresh async engine + sessionmaker per invocation."""
    from src.config import get_settings

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def _run_extract(
    task_id: str,
    cos_object_key: str,
    enable_audio_analysis: bool,
    audio_language: str,
) -> dict:
    from src.models.analysis_task import AnalysisTask, TaskStatus

    factory = _make_session_factory()
    async with factory() as session:
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == UUID(task_id))
            .values(status=TaskStatus.processing, started_at=datetime.now(timezone.utc))
        )
        await session.commit()

        try:
            from src.services.kb_extraction_service import KbExtractionService

            svc = KbExtractionService()
            summary = await svc.extract_knowledge(
                session=session,
                cos_object_key=cos_object_key,
                enable_audio_analysis=enable_audio_analysis,
                audio_language=audio_language,
            )

            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.id == UUID(task_id))
                .values(status=TaskStatus.success, completed_at=datetime.now(timezone.utc))
            )
            await session.commit()
            return {"task_id": task_id, "status": "success", **summary}

        except Exception as exc:  # noqa: BLE001
            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.id == UUID(task_id))
                .values(
                    status=TaskStatus.failed,
                    completed_at=datetime.now(timezone.utc),
                    error_message=str(exc)[:2000],
                )
            )
            await session.commit()
            raise


@shared_task(
    bind=True,
    name="src.workers.kb_extraction_task.extract_kb",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def extract_kb(
    self,
    task_id: str,
    cos_object_key: str,
    enable_audio_analysis: bool = True,
    audio_language: str = "zh",
) -> dict:
    """Extract knowledge base entries from a classified coach video.

    Pre-condition: ``coach_video_classifications.tech_category`` for this
    ``cos_object_key`` must be non-null and != 'unclassified' (enforced at
    submission time by ``ClassificationGateService``).
    """
    logger.info(
        "extract_kb started: task_id=%s cos_object_key=%s audio=%s lang=%s celery_task=%s",
        task_id, cos_object_key, enable_audio_analysis, audio_language, self.request.id,
    )
    try:
        return asyncio.run(
            _run_extract(task_id, cos_object_key, enable_audio_analysis, audio_language)
        )
    except Exception as exc:
        logger.exception("extract_kb failed: task_id=%s error=%s", task_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"task_id": task_id, "status": "failed", "error": str(exc)}
