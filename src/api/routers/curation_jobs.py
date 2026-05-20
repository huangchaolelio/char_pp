"""Feature-021 · curation_jobs router.

集中清洗作业相关的所有 endpoint：

  POST /api/v1/tasks/curation               (US1 单条提交 — body 形如 {coach_video_classification_id, ...})
  POST /api/v1/tasks/curation/batch         (US1 批量提交 — body 形如 {items: [...]})
  GET  /api/v1/curation-jobs/{job_id}       (US1 单作业查询)

后续阶段还将在此文件追加：
  PATCH /api/v1/curation-jobs/{job_id}/segments/{segment_index}  (US4 人工覆盖)

路由层只做请求 → service DTO 的参数转换 + 响应封装；业务逻辑在
:mod:`src.services.curation.curation_service`。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.curation import (
    CurationBatchRejectedItem as _RespRejected,
    CurationBatchRequest,
    CurationBatchResponse,
    CurationBatchSubmittedItem as _RespSubmitted,
    CurationJobDetail,
    CurationJobSummary,
    CurationSegmentItem,
    CurationSubmitRequest,
    CurationSubmitResponse,
)
from src.api.schemas.envelope import SuccessEnvelope, ok
from src.db.session import get_db
from src.services.curation.curation_service import (
    CurationBatchOutcome,
    fetch_curation_job_with_segments,
    submit_curation,
    submit_curation_batch,
)

router = APIRouter(tags=["curation"])


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _to_batch_response(out: CurationBatchOutcome) -> CurationBatchResponse:
    return CurationBatchResponse(
        submitted=[
            _RespSubmitted(
                coach_video_classification_id=s.coach_video_classification_id,
                job_id=s.job_id,
                task_id=s.task_id,
                queued=s.queued,
                idempotent_short_circuit=s.idempotent_short_circuit,
            )
            for s in out.submitted
        ],
        rejected=[
            _RespRejected(
                coach_video_classification_id=r.coach_video_classification_id,
                error_code=r.error_code,
                message=r.message,
            )
            for r in out.rejected
        ],
    )


# ═════════════════════════════════════════════════════════════════════════
# US1 · POST /tasks/curation (single)
# ═════════════════════════════════════════════════════════════════════════


@router.post(
    "/tasks/curation",
    status_code=200,
    response_model=SuccessEnvelope[CurationSubmitResponse],
    summary="提交视频内容清洗任务（单条）",
)
async def submit_single_curation(
    body: CurationSubmitRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[CurationSubmitResponse]:
    out = await submit_curation(
        db,
        classification_id=body.coach_video_classification_id,
        rubric_version=body.curation_rubric_version,
        force=body.force,
    )
    return ok(
        CurationSubmitResponse(
            job_id=out.job_id,
            task_id=out.task_id,
            cos_object_key=out.cos_object_key,
            curation_rubric_version=out.curation_rubric_version,
            status=out.status,
            queued=out.queued,
            idempotent_short_circuit=out.idempotent_short_circuit,
        )
    )


# ═════════════════════════════════════════════════════════════════════════
# US1 · POST /tasks/curation/batch
# ═════════════════════════════════════════════════════════════════════════


@router.post(
    "/tasks/curation/batch",
    status_code=200,
    response_model=SuccessEnvelope[CurationBatchResponse],
    summary="提交视频内容清洗任务（批量）",
)
async def submit_batch_curation(
    body: CurationBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[CurationBatchResponse]:
    items = [it.coach_video_classification_id for it in body.items]
    out = await submit_curation_batch(
        db,
        items=items,
        rubric_version=body.curation_rubric_version,
        force=body.force,
    )
    return ok(_to_batch_response(out))


# ═════════════════════════════════════════════════════════════════════════
# US1 · GET /curation-jobs/{job_id}
# ═════════════════════════════════════════════════════════════════════════


@router.get(
    "/curation-jobs/{job_id}",
    status_code=200,
    response_model=SuccessEnvelope[CurationJobDetail],
    summary="查询单个清洗作业（视频级摘要 + 逐分段判定）",
)
async def get_curation_job(
    job_id: UUID,
    include_segments: bool = Query(
        True,
        description="false 时 segments 数组为空，仅返回视频级摘要",
    ),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[CurationJobDetail]:
    bundle = await fetch_curation_job_with_segments(
        db, job_id, include_segments=include_segments
    )
    if bundle is None:
        raise AppException(
            ErrorCode.NOT_FOUND,
            message="curation job not found",
            details={"resource_id": str(job_id)},
        )

    job, segments, extras = bundle

    summary = CurationJobSummary(
        total_segment_count=job.total_segment_count,
        accepted_segment_count=job.accepted_segment_count,
        rejected_segment_count=job.rejected_segment_count,
        uncertain_segment_count=job.uncertain_segment_count,
        total_duration_seconds=job.total_duration_seconds,
        accepted_duration_seconds=job.accepted_duration_seconds,
        accepted_duration_ratio=job.accepted_duration_ratio,
        low_quality=job.low_quality,
        audio_unavailable=job.audio_unavailable,
        short_video=job.short_video,
        has_overrides=extras["has_overrides"],
        kb_stale_after_override=extras["kb_stale_after_override"],
    )

    detail = CurationJobDetail(
        job_id=job.id,
        cos_object_key=job.cos_object_key,
        coach_video_classification_id=job.coach_video_classification_id,
        preprocessing_job_id=job.preprocessing_job_id,
        curation_rubric_version=job.curation_rubric_version,
        status=job.status,
        error_code=job.error_code,
        error_message=job.error_message,
        summary=summary,
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        segments=[
            CurationSegmentItem(
                segment_index=s.segment_index,
                segment_start_ms=s.segment_start_ms,
                segment_end_ms=s.segment_end_ms,
                auto_decision=s.auto_decision,
                validity_score=s.validity_score,
                rejection_reason=s.rejection_reason,
                decision_source=s.decision_source,
                dim_breakdown=s.dim_breakdown,
                override_decision=s.override_decision,
                override_user=s.override_user,
                override_reason=s.override_reason,
                overridden_at=s.overridden_at,
                effective_decision=s.effective_decision,
            )
            for s in segments
        ],
    )
    return ok(detail)
