"""Celery task: knowledge-base extraction for a classified coach video.

Feature 013 entry point; Feature 014 makes it a thin wrapper that delegates
all DAG work to :class:`Orchestrator`.

Routed to the ``kb_extraction`` queue (capacity 50, concurrency 2). A single
Celery task = one *ExtractionJob* = one slot on the channel. The orchestrator
fans out into 6 sub-steps via asyncio internally; the channel accounting
stays on the job level (FR-015).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from celery import shared_task
from sqlalchemy import select, update
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
    return async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _run_extract(task_id: str, cos_object_key: str) -> dict:
    """Drive the F-014 Orchestrator against the ExtractionJob for this task_id."""
    from src.models.analysis_task import AnalysisTask, TaskStatus
    from src.models.extraction_job import ExtractionJob, ExtractionJobStatus
    from src.services.kb_extraction_pipeline.orchestrator import Orchestrator

    factory = _make_session_factory()
    async with factory() as session:
        # Flip the parent analysis_tasks row to processing.
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == UUID(task_id))
            .values(
                status=TaskStatus.processing,
                started_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

        # Resolve the ExtractionJob linked to this task.
        job_id = (
            await session.execute(
                select(AnalysisTask.extraction_job_id).where(
                    AnalysisTask.id == UUID(task_id)
                )
            )
        ).scalar_one_or_none()
        if job_id is None:
            raise RuntimeError(
                f"analysis_task {task_id} has no extraction_job_id "
                "(Feature-014 create_job was not called by the submission router)"
            )

        orchestrator = Orchestrator()
        try:
            final = await orchestrator.run(session, job_id)
        except Exception as exc:  # noqa: BLE001 — record then re-raise
            logger.exception("orchestrator crashed for job=%s err=%s", job_id, exc)
            # Mark the parent analysis task failed so the channel frees up.
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

        # Mirror the terminal job state onto the analysis_tasks row so the
        # Feature-013 channel service stops counting it as processing.
        if final == ExtractionJobStatus.success:
            parent_status = TaskStatus.success
        else:
            parent_status = TaskStatus.failed
        # Pull the job's error_message so the parent row surfaces it too.
        error_message = (
            await session.execute(
                select(ExtractionJob.error_message).where(ExtractionJob.id == job_id)
            )
        ).scalar_one_or_none()
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == UUID(task_id))
            .values(
                status=parent_status,
                completed_at=datetime.now(timezone.utc),
                error_message=error_message,
            )
        )
        await session.commit()

        return {
            "task_id": task_id,
            "job_id": str(job_id),
            "status": final.value,
        }


@shared_task(
    bind=True,
    name="src.workers.kb_extraction_task.extract_kb",
    max_retries=0,  # F-014: retries are handled inside the orchestrator.
    acks_late=True,
    soft_time_limit=2800,  # job timeout (2700s) + 100s grace
    time_limit=2820,
)
def extract_kb(
    self,
    task_id: str,
    cos_object_key: str,
    enable_audio_analysis: bool = True,
    audio_language: str = "zh",
) -> dict:
    """Extract knowledge-base entries from a classified coach video.

    Pre-conditions:
      - ``coach_video_classifications.tech_category`` must be non-null and
        != 'unclassified' for this ``cos_object_key`` (enforced by the
        submission router's ClassificationGateService).
      - ``analysis_tasks.extraction_job_id`` must already be populated by the
        submission router calling ``Orchestrator.create_job`` in the same
        transaction as the INSERT into ``analysis_tasks``.
    """
    # ``enable_audio_analysis`` / ``audio_language`` are persisted on the
    # ExtractionJob row by the submission router; we accept them as kwargs
    # only for backwards compatibility with the Feature-013 call signature.
    _ = (enable_audio_analysis, audio_language)
    logger.info(
        "extract_kb started: task_id=%s cos_object_key=%s celery_task=%s",
        task_id,
        cos_object_key,
        self.request.id,
    )
    try:
        return asyncio.run(_run_extract(task_id, cos_object_key))
    except Exception as exc:
        logger.exception("extract_kb failed: task_id=%s error=%s", task_id, exc)
        # No Celery-level retry — orchestrator already persisted the failure.
        return {"task_id": task_id, "status": "failed", "error": str(exc)[:2000]}
