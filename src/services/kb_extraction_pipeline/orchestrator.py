"""Feature 014 — DAG orchestrator + ExtractionJob factory.

Design (research.md R1/R2):
  - Static 6-step DAG defined in ``pipeline_definition``.
  - ``create_job`` builds the job + 6 ``pipeline_steps`` rows (one per step type)
    and links the parent ``analysis_tasks`` row via ``extraction_job_id``.
  - ``run`` executes the DAG using ``asyncio.gather`` for each topological wave
    (steps whose deps are all ``success``). Timeouts are enforced at both the
    job (45 min) and step (10 min) levels via ``asyncio.wait_for``.
  - Failed steps propagate as ``skipped`` to their downstream set. The
    ``merge_kb`` step has a degradation mode: when ``audio_kb_extract`` is
    failed/skipped but ``visual_kb_extract`` succeeded, ``merge_kb`` still runs.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.models.analysis_task import AnalysisTask
from src.models.extraction_job import ExtractionJob, ExtractionJobStatus
from src.models.kb_conflict import KbConflict
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.services.kb_extraction_pipeline.pipeline_definition import (
    DEPENDENCIES,
    all_step_types,
    dependents_of,
)
from src.services.kb_extraction_pipeline.retry_policy import run_with_retry
from src.utils.time_utils import now_cst

logger = logging.getLogger(__name__)


# ── Step-executor dispatch (lazy import to avoid circular loads) ────────────

def _dispatch_executor(step_type: StepType):
    """Return the async ``execute(session, job, step)`` function for a step."""
    # Imports are local so models/config can load without executor deps.
    from src.services.kb_extraction_pipeline.step_executors import (
        audio_kb_extract,
        audio_transcription,
        download_video,
        merge_kb,
        pose_analysis,
        visual_kb_extract,
    )

    table = {
        StepType.download_video: download_video.execute,
        StepType.pose_analysis: pose_analysis.execute,
        StepType.audio_transcription: audio_transcription.execute,
        StepType.visual_kb_extract: visual_kb_extract.execute,
        StepType.audio_kb_extract: audio_kb_extract.execute,
        StepType.merge_kb: merge_kb.execute,
    }
    return table[step_type]


# ── Result DTOs ─────────────────────────────────────────────────────────────


class StepFailure(RuntimeError):
    """Raised when a step's executor fails after the retry policy is exhausted."""


class Orchestrator:
    """DAG runner + job factory (async)."""

    # ══════════════════════════════════════════════════════════════════════
    # Creation API — called from the task submission router
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    async def create_job(
        cls,
        session: AsyncSession,
        *,
        analysis_task_id: UUID,
        cos_object_key: str,
        tech_category: str,
        enable_audio_analysis: bool = True,
        audio_language: str = "zh",
        force: bool = False,
    ) -> ExtractionJob:
        """Create an ExtractionJob + 6 PipelineStep rows; link the analysis_tasks row.

        When ``force=True`` and a previous ``success`` job exists for the same
        ``cos_object_key``, that job is marked ``superseded_by_job_id`` and all
        of its unresolved kb_conflicts rows are tagged too (BR-10).
        """
        # Guard: the analysis_tasks row must exist and be kb_extraction type
        # (router is expected to validate; we defensively check for UNIQUE FK).
        job = ExtractionJob(
            analysis_task_id=analysis_task_id,
            cos_object_key=cos_object_key,
            tech_category=tech_category,
            status=ExtractionJobStatus.pending,
            enable_audio_analysis=enable_audio_analysis,
            audio_language=audio_language,
            force=force,
        )
        session.add(job)
        await session.flush()  # assigns job.id

        # Seed the 6 DAG nodes.
        for step_type in all_step_types():
            session.add(
                PipelineStep(
                    job_id=job.id,
                    step_type=step_type,
                    status=PipelineStepStatus.pending,
                )
            )

        # Link back to the analysis_tasks row (extraction_job_id FK).
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == analysis_task_id)
            .values(extraction_job_id=job.id)
        )

        # Force-supersede any prior success job for the same COS key.
        if force:
            await cls._supersede_previous_success_jobs(session, cos_object_key, job.id)

        return job

    @staticmethod
    async def _supersede_previous_success_jobs(
        session: AsyncSession,
        cos_object_key: str,
        new_job_id: UUID,
    ) -> None:
        await session.execute(
            update(ExtractionJob)
            .where(
                ExtractionJob.cos_object_key == cos_object_key,
                ExtractionJob.status == ExtractionJobStatus.success,
                ExtractionJob.id != new_job_id,
                ExtractionJob.superseded_by_job_id.is_(None),
            )
            .values(superseded_by_job_id=new_job_id)
        )
        await session.execute(
            update(KbConflict)
            .where(
                KbConflict.cos_object_key == cos_object_key,
                KbConflict.resolved_at.is_(None),
                KbConflict.superseded_by_job_id.is_(None),
            )
            .values(superseded_by_job_id=new_job_id)
        )

    # ══════════════════════════════════════════════════════════════════════
    # Run API — called from the Celery worker
    # ══════════════════════════════════════════════════════════════════════

    async def run(self, session: AsyncSession, job_id: UUID) -> ExtractionJobStatus:
        """Execute the DAG for a job, blocking until terminal (success/failed).

        ``session`` is used for job-level state transitions (mark running /
        finalize). Inside the driver loop we spin up a **fresh AsyncSession
        per parallel step** so pose / audio / KB extractors can genuinely run
        concurrently — a single shared session would serialise every
        ``session.execute`` and collapse the DAG to sequential semantics.
        """
        settings = get_settings()

        # Derive a sessionmaker bound to the same engine as the caller's
        # session. This lets us open one short-lived session per parallel
        # branch without plumbing a factory argument through every caller.
        step_session_factory = async_sessionmaker(
            session.bind, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )

        # Mark the job as running (idempotent — reruns also pass here).
        now = now_cst()
        job_row = (
            await session.execute(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
        ).scalar_one()
        job_values: dict = {
            "status": ExtractionJobStatus.running,
            "worker_hostname": socket.gethostname(),
            "error_message": None,
        }
        if job_row.started_at is None:
            job_values["started_at"] = now
        await session.execute(
            update(ExtractionJob).where(ExtractionJob.id == job_id).values(**job_values)
        )
        await session.commit()

        try:
            await asyncio.wait_for(
                self._drive_loop(session, step_session_factory, job_id),
                timeout=settings.extraction_job_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error("extraction_job %s hit 45min job timeout", job_id)
            await self._mark_unfinished_as_failed(
                session, job_id, reason="job timeout after 45min"
            )

        # Final reconciliation + retention timestamps.
        final_status = await self._finalize_job(session, job_id)
        return final_status

    async def _drive_loop(
        self,
        session: AsyncSession,
        step_session_factory: async_sessionmaker[AsyncSession],
        job_id: UUID,
    ) -> None:
        """Keep picking ready steps until no step is pending or running."""
        while True:
            steps_by_type = await self._load_steps(session, job_id)
            ready = self._find_ready_steps(steps_by_type)
            if not ready:
                # Nothing ready — if nothing is running either, we're done.
                if not any(
                    s.status == PipelineStepStatus.running for s in steps_by_type.values()
                ):
                    return
                await asyncio.sleep(0.5)
                continue

            # Launch all ready steps in parallel. Each gets its own
            # AsyncSession so they don't queue on a shared connection.
            await asyncio.gather(
                *(
                    self._execute_step(step_session_factory, job_id, s)
                    for s in ready
                ),
                return_exceptions=True,  # per-step errors are recorded in DB
            )

            # Invalidate the driver session's cache so the next
            # _load_steps sees the state writes committed by the parallel
            # step sessions above. Without this, the ORM identity map
            # serves stale rows and the loop stalls.
            session.expire_all()

    async def _load_steps(
        self, session: AsyncSession, job_id: UUID
    ) -> dict[StepType, PipelineStep]:
        rows = (
            await session.execute(
                select(PipelineStep).where(PipelineStep.job_id == job_id)
            )
        ).scalars().all()
        return {row.step_type: row for row in rows}

    def _find_ready_steps(
        self, steps_by_type: dict[StepType, PipelineStep]
    ) -> list[PipelineStep]:
        """Return pending steps whose upstream dependencies are already ``success``.

        Special case for ``merge_kb``: also ready when the visual path is
        success and the audio path is either failed or skipped (degradation
        mode, FR-012).
        """
        ready: list[PipelineStep] = []
        for step_type, step in steps_by_type.items():
            if step.status != PipelineStepStatus.pending:
                continue

            if step_type == StepType.merge_kb:
                visual = steps_by_type[StepType.visual_kb_extract]
                audio = steps_by_type[StepType.audio_kb_extract]
                if visual.status != PipelineStepStatus.success:
                    continue  # visual is the hard requirement
                # merge_kb is ready when audio is also success OR it has
                # reached a terminal non-success state (skipped/failed).
                if audio.status not in {
                    PipelineStepStatus.success,
                    PipelineStepStatus.failed,
                    PipelineStepStatus.skipped,
                }:
                    continue
                ready.append(step)
                continue

            deps = DEPENDENCIES[step_type]
            if all(
                steps_by_type[d].status == PipelineStepStatus.success for d in deps
            ):
                ready.append(step)
        return ready

    async def _execute_step(
        self,
        step_session_factory: async_sessionmaker[AsyncSession],
        job_id: UUID,
        step: PipelineStep,
    ) -> None:
        """Execute one step with timeout + retry, recording terminal state to DB.

        The step owns a private ``AsyncSession`` — this is what makes the
        DAG actually parallel. Every ``await`` inside the executor hits its
        own connection instead of queuing on the driver's.
        """
        settings = get_settings()
        started = now_cst()

        async with step_session_factory() as session:
            await session.execute(
                update(PipelineStep)
                .where(PipelineStep.id == step.id)
                .values(
                    status=PipelineStepStatus.running,
                    started_at=started,
                    error_message=None,
                )
            )
            await session.commit()

            executor = _dispatch_executor(step.step_type)
            fresh_job = (
                await session.execute(
                    select(ExtractionJob).where(ExtractionJob.id == job_id)
                )
            ).scalar_one()
            fresh_step = (
                await session.execute(
                    select(PipelineStep).where(PipelineStep.id == step.id)
                )
            ).scalar_one()

            try:
                output = await asyncio.wait_for(
                    run_with_retry(
                        step.step_type,
                        lambda: executor(session, fresh_job, fresh_step),
                    ),
                    timeout=settings.extraction_step_timeout_seconds,
                )
                final_status = output.get("status", PipelineStepStatus.success)
                await self._mark_step_terminal(
                    session,
                    step,
                    status=final_status,
                    output_summary=output.get("output_summary"),
                    output_artifact_path=output.get("output_artifact_path"),
                    started_at=started,
                )
                # A self-reported ``skipped`` (e.g. audio disabled or no audio
                # track) propagates downstream exactly like a failure — except
                # ``merge_kb`` still runs in degradation mode.
                if final_status == PipelineStepStatus.skipped:
                    await self._propagate_skipped(session, job_id, step.step_type)
            except asyncio.TimeoutError:
                await self._mark_step_terminal(
                    session,
                    step,
                    status=PipelineStepStatus.failed,
                    error_message=(
                        f"step timeout after "
                        f"{settings.extraction_step_timeout_seconds}s"
                    ),
                    started_at=started,
                )
                await self._propagate_skipped(session, job_id, step.step_type)
            except Exception as exc:  # noqa: BLE001 — record and propagate
                logger.exception(
                    "step %s failed job=%s err=%s",
                    step.step_type.value, job_id, exc,
                )
                # The session may be in a poisoned (rolled-back) state if the
                # executor raised after partial writes. Recover before we try
                # to record the failure.
                await session.rollback()
                await self._mark_step_terminal(
                    session,
                    step,
                    status=PipelineStepStatus.failed,
                    error_message=str(exc)[:2000],
                    started_at=started,
                )
                await self._propagate_skipped(session, job_id, step.step_type)

    async def _mark_step_terminal(
        self,
        session: AsyncSession,
        step: PipelineStep,
        *,
        status: PipelineStepStatus,
        output_summary: dict | None = None,
        output_artifact_path: str | None = None,
        error_message: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        completed = now_cst()
        duration_ms: int | None = None
        if started_at is not None:
            duration_ms = int((completed - started_at).total_seconds() * 1000)
        await session.execute(
            update(PipelineStep)
            .where(PipelineStep.id == step.id)
            .values(
                status=status,
                output_summary=output_summary,
                output_artifact_path=output_artifact_path,
                error_message=error_message,
                completed_at=completed,
                duration_ms=duration_ms,
            )
        )
        await session.commit()

    async def _propagate_skipped(
        self, session: AsyncSession, job_id: UUID, failed_step_type: StepType
    ) -> None:
        """Mark transitively-downstream pending steps as ``skipped``.

        ``merge_kb`` is treated specially: we do NOT skip it when only the
        audio path failed, so the degradation mode can still run.
        """
        # BFS over the dependents graph.
        downstream: set[StepType] = set()
        queue: list[StepType] = list(dependents_of(failed_step_type))
        while queue:
            nxt = queue.pop()
            if nxt in downstream:
                continue
            downstream.add(nxt)
            queue.extend(dependents_of(nxt))

        # Don't blanket-skip merge_kb: it runs in degradation mode when
        # the visual path still succeeded.
        if StepType.merge_kb in downstream and failed_step_type in {
            StepType.audio_transcription,
            StepType.audio_kb_extract,
        }:
            downstream.discard(StepType.merge_kb)

        if not downstream:
            return
        await session.execute(
            update(PipelineStep)
            .where(
                PipelineStep.job_id == job_id,
                PipelineStep.step_type.in_(list(downstream)),
                PipelineStep.status == PipelineStepStatus.pending,
            )
            .values(status=PipelineStepStatus.skipped)
        )
        await session.commit()

    async def _mark_unfinished_as_failed(
        self, session: AsyncSession, job_id: UUID, *, reason: str
    ) -> None:
        """Forcefully terminate any ``pending``/``running`` step on job timeout."""
        await session.execute(
            update(PipelineStep)
            .where(
                PipelineStep.job_id == job_id,
                PipelineStep.status.in_(
                    [PipelineStepStatus.pending, PipelineStepStatus.running]
                ),
            )
            .values(
                status=PipelineStepStatus.failed,
                error_message=reason,
                completed_at=now_cst(),
            )
        )
        await session.commit()

    async def _finalize_job(
        self, session: AsyncSession, job_id: UUID
    ) -> ExtractionJobStatus:
        """Compute the terminal job status from step states + write timestamps."""
        settings = get_settings()
        steps_by_type = await self._load_steps(session, job_id)
        merge = steps_by_type[StepType.merge_kb]

        if merge.status == PipelineStepStatus.success:
            final = ExtractionJobStatus.success
            retention = timedelta(hours=settings.extraction_success_retention_hours)
            error_msg: str | None = None
        else:
            final = ExtractionJobStatus.failed
            retention = timedelta(hours=settings.extraction_failed_retention_hours)
            # Surface the earliest failure reason on the job.
            first_fail = next(
                (
                    s for s in steps_by_type.values()
                    if s.status == PipelineStepStatus.failed and s.error_message
                ),
                None,
            )
            error_msg = (
                f"{first_fail.step_type.value}: {first_fail.error_message}"
                if first_fail
                else "merge_kb did not succeed"
            )

        completed = now_cst()
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(
                status=final,
                completed_at=completed,
                intermediate_cleanup_at=completed + retention,
                error_message=error_msg,
            )
        )
        await session.commit()
        return final
