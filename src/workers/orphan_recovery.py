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
    """Mark stale ``processing`` rows as ``failed``; return the number reclaimed.

    Feature-014 extension: also sweep ``pipeline_steps`` that are stuck in
    ``running`` past the per-step timeout. For each such step we:
      - flip it to ``failed`` (with a clear orphan error message)
      - propagate ``skipped`` to its transitive downstream
      - mark the parent ``extraction_jobs`` row ``failed``
      - mark the parent ``analysis_tasks`` row ``failed`` so the kb_extraction
        channel frees the slot
    """
    from src.config import get_settings
    from src.models.analysis_task import AnalysisTask, TaskStatus

    settings = get_settings()
    timeout_s = settings.orphan_task_timeout_seconds
    step_timeout_s = settings.extraction_step_timeout_seconds
    cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=timeout_s)
    step_cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=step_timeout_s)

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

            # Feature 014: sweep stuck pipeline_steps.
            pipeline_orphans = await _sweep_pipeline_step_orphans(
                session, step_cutoff
            )
            return count + pipeline_orphans
    finally:
        await engine.dispose()


async def _sweep_pipeline_step_orphans(
    session: AsyncSession, step_cutoff: datetime
) -> int:
    """Reclaim ``pipeline_steps`` rows stuck in ``running`` past the step timeout.

    Returns the number of stuck steps that were reclaimed.
    """
    from sqlalchemy import select

    from src.models.analysis_task import AnalysisTask, TaskStatus
    from src.models.extraction_job import ExtractionJob, ExtractionJobStatus
    from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
    from src.services.kb_extraction_pipeline.pipeline_definition import (
        dependents_of,
    )

    stuck = (
        await session.execute(
            select(PipelineStep).where(
                PipelineStep.status == PipelineStepStatus.running,
                PipelineStep.started_at.isnot(None),
                PipelineStep.started_at < step_cutoff,
            )
        )
    ).scalars().all()

    if not stuck:
        return 0

    now = datetime.now(tz=timezone.utc)
    affected_job_ids: set = set()
    for step in stuck:
        # Flip stuck step to failed.
        step.status = PipelineStepStatus.failed
        step.error_message = "orphan recovered: worker crashed mid-step"
        step.completed_at = now
        affected_job_ids.add(step.job_id)

        # Propagate skipped to transitive downstream (BFS over dependents).
        downstream: set[StepType] = set()
        queue: list[StepType] = list(dependents_of(step.step_type))
        while queue:
            nxt = queue.pop()
            if nxt in downstream:
                continue
            downstream.add(nxt)
            queue.extend(dependents_of(nxt))

        # merge_kb should degrade (not skip) when only the audio path failed —
        # this matches the orchestrator's live-run semantics.
        if step.step_type in {
            StepType.audio_transcription,
            StepType.audio_kb_extract,
        }:
            downstream.discard(StepType.merge_kb)

        if downstream:
            await session.execute(
                update(PipelineStep)
                .where(
                    PipelineStep.job_id == step.job_id,
                    PipelineStep.step_type.in_(list(downstream)),
                    PipelineStep.status == PipelineStepStatus.pending,
                )
                .values(status=PipelineStepStatus.skipped)
            )

    # Mark the affected jobs (and their parent analysis_tasks rows) failed.
    for job_id in affected_job_ids:
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(
                status=ExtractionJobStatus.failed,
                completed_at=now,
                error_message="orphan recovery: one or more steps were stuck",
            )
        )

        parent_id = (
            await session.execute(
                select(ExtractionJob.analysis_task_id).where(
                    ExtractionJob.id == job_id
                )
            )
        ).scalar_one_or_none()
        if parent_id is not None:
            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.id == parent_id)
                .values(
                    status=TaskStatus.failed,
                    completed_at=now,
                    error_message="orphan recovery: pipeline step timed out",
                )
            )

    await session.commit()
    logger.warning(
        "orphan recovery: reclaimed %d stuck pipeline_steps (jobs=%d)",
        len(stuck), len(affected_job_ids),
    )
    return len(stuck)


def sweep_orphan_tasks_sync() -> int:
    """Blocking wrapper for use in Celery signal handlers."""
    return asyncio.run(sweep_orphan_tasks())
