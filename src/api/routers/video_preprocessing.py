"""Feature-016 — GET /api/v1/video-preprocessing/{job_id}.

Read-only audit endpoint that returns full metadata for one preprocessing
job: original_meta + target_standard + audio descriptor + segment list.
See contracts/get_preprocessing_job.md.

Feature-017：响应体统一切换为 ``SuccessEnvelope``（章程 v1.4.0 原则 IX）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.envelope import SuccessEnvelope, ok
from src.api.schemas.preprocessing import (
    PreprocessingAudioView,
    PreprocessingJobResponse,
    PreprocessingOriginalMeta,
    PreprocessingSegmentView,
    PreprocessingTargetStandard,
)
from src.db.session import get_db
from src.services import preprocessing_service as _preprocessing_service


router = APIRouter(tags=["video-preprocessing"])


@router.get(
    "/video-preprocessing/{job_id}",
    response_model=SuccessEnvelope[PreprocessingJobResponse],
    summary="Get full metadata for one preprocessing job",
)
async def get_preprocessing_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[PreprocessingJobResponse]:
    view = await _preprocessing_service.get_job_view(db, job_id)
    if view is None:
        raise AppException(
            ErrorCode.PREPROCESSING_JOB_NOT_FOUND,
            details={"resource_id": str(job_id)},
        )

    original = None
    if view.original_meta:
        original = PreprocessingOriginalMeta(**view.original_meta)
    target = None
    if view.target_standard:
        target = PreprocessingTargetStandard(**view.target_standard)
    audio = None
    if view.audio:
        audio = PreprocessingAudioView(**view.audio)

    return ok(PreprocessingJobResponse(
        job_id=view.job_id,
        cos_object_key=view.cos_object_key,
        status=view.status,
        force=view.force,
        started_at=view.started_at,
        completed_at=view.completed_at,
        duration_ms=view.duration_ms,
        segment_count=view.segment_count,
        has_audio=view.has_audio,
        error_message=view.error_message,
        original_meta=original,
        target_standard=target,
        audio=audio,
        segments=[
            PreprocessingSegmentView(
                segment_index=s.segment_index,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                cos_object_key=s.cos_object_key,
                size_bytes=s.size_bytes,
            )
            for s in view.segments
        ],
    ))


# ── Exposed to the contract test as an AsyncMock target ─────────────────────
# ``test_preprocessing_api.py`` patches
# ``src.api.routers.video_preprocessing._preprocessing_service.get_job_view``.
# Keep the reference alive so the patch target resolves.
_preprocessing_service = _preprocessing_service  # noqa: PLW0127
