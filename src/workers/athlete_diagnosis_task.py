"""Celery task: athlete motion diagnosis (Feature 013).

Routed to the ``diagnosis`` queue (capacity 20, concurrency 2).

Flow mirrors ``extract_kb``:
  1. Mark ``analysis_tasks`` row ``processing``.
  2. Delegate to ``DiagnosisService.diagnose_athlete_video`` (wired in US3/T040/T041).
  3. On success/failure, write final status + ``completed_at``.
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
    from src.config import get_settings

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def _run_diagnose(
    task_id: str,
    video_storage_uri: str,
    knowledge_base_version: str | None,
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
            try:
                from src.services.diagnosis_service import DiagnosisService

                svc = DiagnosisService(session)
                summary = await svc.diagnose_athlete_video(
                    session=session,
                    task_id=UUID(task_id),
                    video_storage_uri=video_storage_uri,
                    knowledge_base_version=knowledge_base_version,
                )
            except AttributeError:
                # Defensive: if the service module shape drifts and the
                # ``diagnose_athlete_video`` entry disappears, log once and
                # fail the task so ops notices rather than silently "skeleton".
                logger.exception(
                    "DiagnosisService.diagnose_athlete_video missing for task %s",
                    task_id,
                )
                raise

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
    name="src.workers.athlete_diagnosis_task.diagnose_athlete",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def diagnose_athlete(
    self,
    task_id: str,
    video_storage_uri: str,
    knowledge_base_version: str | None = None,
) -> dict:
    """Diagnose an athlete video against the tech standard knowledge base."""
    logger.info(
        "diagnose_athlete started: task_id=%s uri=%s kb_ver=%s celery_task=%s",
        task_id, video_storage_uri, knowledge_base_version, self.request.id,
    )
    try:
        return asyncio.run(
            _run_diagnose(task_id, video_storage_uri, knowledge_base_version)
        )
    except Exception as exc:
        logger.exception("diagnose_athlete failed: task_id=%s error=%s", task_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"task_id": task_id, "status": "failed", "error": str(exc)}
