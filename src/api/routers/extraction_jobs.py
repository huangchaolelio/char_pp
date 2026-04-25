"""Feature 014 — /api/v1/extraction-jobs router.

Endpoints:
  - GET  /extraction-jobs/{job_id}        (FR-003)
  - GET  /extraction-jobs                 (FR-023: paginated list)
  - POST /extraction-jobs/{job_id}/rerun  (FR-005; US4 adds real logic)

The submission endpoint lives in ``tasks.py`` (POST /tasks/kb-extraction) —
see research.md R10 for the separation-of-concerns rationale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.extraction_job import (
    ErrorDetail,
    ErrorResponse,
    ExtractionJobDetail,
    ExtractionJobListResponse,
    ExtractionJobSummary,
    PipelineStepResponse,
    ProgressResponse,
    RerunRequest,
    RerunResponse,
)
from src.db.session import get_db
from src.models.extraction_job import ExtractionJob, ExtractionJobStatus
from src.models.kb_conflict import KbConflict
from src.models.pipeline_step import PipelineStep, PipelineStepStatus
from src.services.kb_extraction_pipeline.pipeline_definition import DEPENDENCIES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["extraction-jobs"])


# ── Helpers ─────────────────────────────────────────────────────────────────


def _progress_from_steps(steps: list[PipelineStep]) -> ProgressResponse:
    counts = {s: 0 for s in PipelineStepStatus}
    for step in steps:
        counts[step.status] += 1
    total = len(steps)
    # A step counts as "done" when terminal (success | failed | skipped).
    done = (
        counts[PipelineStepStatus.success]
        + counts[PipelineStepStatus.failed]
        + counts[PipelineStepStatus.skipped]
    )
    return ProgressResponse(
        total_steps=total,
        success_steps=counts[PipelineStepStatus.success],
        failed_steps=counts[PipelineStepStatus.failed],
        skipped_steps=counts[PipelineStepStatus.skipped],
        running_steps=counts[PipelineStepStatus.running],
        pending_steps=counts[PipelineStepStatus.pending],
        percent=round(done / total, 4) if total else 0.0,
    )


def _step_to_response(step: PipelineStep) -> PipelineStepResponse:
    deps = DEPENDENCIES.get(step.step_type, [])
    return PipelineStepResponse(
        step_type=step.step_type.value,
        status=step.status.value,
        retry_count=step.retry_count,
        error_message=step.error_message,
        output_summary=step.output_summary,
        output_artifact_path=step.output_artifact_path,
        started_at=step.started_at,
        completed_at=step.completed_at,
        duration_ms=step.duration_ms,
        depends_on=[d.value for d in deps],
    )


async def _conflict_count(session: AsyncSession, job_id: UUID) -> int:
    return (
        await session.execute(
            select(func.count(KbConflict.id)).where(
                KbConflict.job_id == job_id,
                KbConflict.resolved_at.is_(None),
                KbConflict.superseded_by_job_id.is_(None),
            )
        )
    ).scalar_one()


# ── GET /extraction-jobs/{job_id} ───────────────────────────────────────────


@router.get(
    "/extraction-jobs/{job_id}",
    response_model=ExtractionJobDetail,
    responses={404: {"model": ErrorResponse}},
    summary="单作业详情（含子任务 + 冲突计数）",
)
async def get_extraction_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ExtractionJobDetail:
    job = (
        await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "JOB_NOT_FOUND",
                    "message": f"extraction job {job_id} not found",
                    "details": {"job_id": str(job_id)},
                }
            },
        )

    steps = (
        await db.execute(
            select(PipelineStep).where(PipelineStep.job_id == job_id)
        )
    ).scalars().all()

    progress = _progress_from_steps(list(steps))
    conflict_count = await _conflict_count(db, job_id)

    return ExtractionJobDetail(
        job_id=job.id,
        analysis_task_id=job.analysis_task_id,
        cos_object_key=job.cos_object_key,
        tech_category=job.tech_category,
        status=job.status.value,
        worker_hostname=job.worker_hostname,
        enable_audio_analysis=job.enable_audio_analysis,
        audio_language=job.audio_language,
        force=job.force,
        superseded_by_job_id=job.superseded_by_job_id,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        intermediate_cleanup_at=job.intermediate_cleanup_at,
        steps=[_step_to_response(s) for s in steps],
        progress=progress,
        conflict_count=conflict_count,
    )


# ── GET /extraction-jobs  (paginated list) ──────────────────────────────────


@router.get(
    "/extraction-jobs",
    response_model=ExtractionJobListResponse,
    summary="作业列表（分页 + 状态过滤）",
)
async def list_extraction_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ExtractionJobListResponse:
    # Optional status filter.
    status_enum: Optional[ExtractionJobStatus] = None
    if status:
        try:
            status_enum = ExtractionJobStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "INVALID_STATUS",
                        "message": f"unknown status filter: {status!r}",
                        "details": {
                            "allowed": [s.value for s in ExtractionJobStatus]
                        },
                    }
                },
            )

    base_q = select(ExtractionJob)
    if status_enum:
        base_q = base_q.where(ExtractionJob.status == status_enum)

    total = (
        await db.execute(
            select(func.count()).select_from(base_q.subquery())
        )
    ).scalar_one()

    rows = (
        await db.execute(
            base_q.order_by(ExtractionJob.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).scalars().all()

    # Precompute duration + conflict counts in-memory (small page sizes).
    items: list[ExtractionJobSummary] = []
    for job in rows:
        duration_ms: int | None = None
        if job.started_at and job.completed_at:
            duration_ms = int(
                (job.completed_at - job.started_at).total_seconds() * 1000
            )
        conflict_count = await _conflict_count(db, job.id)
        items.append(
            ExtractionJobSummary(
                job_id=job.id,
                analysis_task_id=job.analysis_task_id,
                cos_object_key=job.cos_object_key,
                tech_category=job.tech_category,
                status=job.status.value,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                duration_ms=duration_ms,
                conflict_count=conflict_count,
                error_message=job.error_message,
            )
        )

    return ExtractionJobListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


# ── POST /extraction-jobs/{job_id}/rerun (Feature 014 US4) ──────────────────


@router.post(
    "/extraction-jobs/{job_id}/rerun",
    response_model=RerunResponse,
    status_code=202,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
    summary="重跑失败作业（US4）",
)
async def rerun_extraction_job(
    job_id: UUID,
    body: RerunRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> RerunResponse:
    """Re-run a failed ExtractionJob.

    Default behaviour resets only ``failed`` + ``skipped`` pipeline_steps and
    keeps any ``success`` step's output_summary / artifact path intact so the
    orchestrator skips them on the next pass (FR-005, SC-005).

    When ``force_from_scratch=true`` ALL 6 steps reset to ``pending`` and
    their artifacts are cleared — required when the intermediate retention
    window has expired and the local files are gone.

    Responses:
      - 202: rerun scheduled; returns the job id + which steps were reset
      - 404: JOB_NOT_FOUND
      - 409 JOB_NOT_FAILED: job is not in ``failed`` state, refuse
      - 409 INTERMEDIATE_EXPIRED: retention window passed and caller did not
        pass ``force_from_scratch=true``
    """
    from datetime import datetime, timezone

    from sqlalchemy import update as _sql_update

    from src.models.analysis_task import AnalysisTask, TaskStatus
    from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType

    rerun_body = body or RerunRequest()

    job = (
        await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "JOB_NOT_FOUND",
                    "message": f"extraction job {job_id} not found",
                    "details": {"job_id": str(job_id)},
                }
            },
        )

    if job.status != ExtractionJobStatus.failed:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "JOB_NOT_FAILED",
                    "message": (
                        "rerun is only valid for jobs in 'failed' state; "
                        f"this job is {job.status.value!r}"
                    ),
                    "details": {
                        "job_id": str(job_id),
                        "current_status": job.status.value,
                    },
                }
            },
        )

    # Intermediate-retention guard (FR-013 + Q5).
    now = datetime.now(timezone.utc)
    expired = (
        job.intermediate_cleanup_at is not None
        and job.intermediate_cleanup_at <= now
    )
    if expired and not rerun_body.force_from_scratch:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "INTERMEDIATE_EXPIRED",
                    "message": (
                        "intermediate artifacts were cleaned up; retry with "
                        "force_from_scratch=true to re-execute the whole DAG"
                    ),
                    "details": {
                        "job_id": str(job_id),
                        "intermediate_cleanup_at": (
                            job.intermediate_cleanup_at.isoformat()
                        ),
                        "rerun_hint": "force_from_scratch=true",
                    },
                }
            },
        )

    # Load all 6 steps so we can decide what to reset.
    steps = (
        await db.execute(
            select(PipelineStep).where(PipelineStep.job_id == job_id)
        )
    ).scalars().all()

    reset_types: list[StepType] = []
    if rerun_body.force_from_scratch:
        # Every step → pending, wipe artifacts so executors re-run fresh.
        for s in steps:
            reset_types.append(s.step_type)
        await db.execute(
            _sql_update(PipelineStep)
            .where(PipelineStep.job_id == job_id)
            .values(
                status=PipelineStepStatus.pending,
                started_at=None,
                completed_at=None,
                duration_ms=None,
                error_message=None,
                output_summary=None,
                output_artifact_path=None,
                retry_count=0,
            )
        )
    else:
        # Default: reset failed + skipped back to pending; keep success rows.
        for s in steps:
            if s.status in {
                PipelineStepStatus.failed,
                PipelineStepStatus.skipped,
            }:
                reset_types.append(s.step_type)
        if reset_types:
            await db.execute(
                _sql_update(PipelineStep)
                .where(
                    PipelineStep.job_id == job_id,
                    PipelineStep.step_type.in_(reset_types),
                )
                .values(
                    status=PipelineStepStatus.pending,
                    started_at=None,
                    completed_at=None,
                    duration_ms=None,
                    error_message=None,
                    # Keep output_summary / output_artifact_path NULL on a
                    # retry (they were already NULL for failed/skipped).
                    retry_count=0,
                )
            )

    # Flip job back into running state so subsequent status queries and
    # Feature-013 channel counters agree with reality.
    await db.execute(
        _sql_update(ExtractionJob)
        .where(ExtractionJob.id == job_id)
        .values(
            status=ExtractionJobStatus.running,
            error_message=None,
            completed_at=None,
            intermediate_cleanup_at=None,
        )
    )
    # Parent analysis_tasks row: flip back to pending so channel accounting
    # treats this as active work (FR-016 — no extra channel slot consumed).
    await db.execute(
        _sql_update(AnalysisTask)
        .where(AnalysisTask.id == job.analysis_task_id)
        .values(
            status=TaskStatus.pending,
            error_message=None,
            completed_at=None,
            started_at=None,
        )
    )
    await db.commit()

    # Kick Celery — rerun uses the same task_id + cos_object_key as the
    # original submission. Import is lazy to avoid pulling Celery at module
    # load.
    from src.workers.kb_extraction_task import extract_kb

    extract_kb.apply_async(
        kwargs={
            "task_id": str(job.analysis_task_id),
            "cos_object_key": job.cos_object_key,
        },
        queue="kb_extraction",
    )

    # Sort reset_steps in topological order for a stable response.
    from src.services.kb_extraction_pipeline.pipeline_definition import (
        TOPOLOGICAL_ORDER,
    )
    order_index = {t: i for i, t in enumerate(TOPOLOGICAL_ORDER)}
    reset_types.sort(key=lambda t: order_index.get(t, 99))

    return RerunResponse(
        job_id=job_id,
        status="running",
        reset_steps=[t.value for t in reset_types],
    )
