"""Feature-016 — GET /api/v1/video-preprocessing/{job_id}.

Read-only audit endpoint that returns full metadata for one preprocessing
job: original_meta + target_standard + audio descriptor + segment list.
See contracts/get_preprocessing_job.md.

Feature-017：响应体统一切换为 ``SuccessEnvelope``（章程 v1.4.0 原则 IX）。
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.envelope import SuccessEnvelope, ok, page as page_envelope
from src.api.schemas.preprocessing import (
    PreprocessingAudioView,
    PreprocessingJobListItem,
    PreprocessingJobResponse,
    PreprocessingOriginalMeta,
    PreprocessingSegmentView,
    PreprocessingTargetStandard,
)
from src.db.session import get_db
from src.services import preprocessing_service as _preprocessing_service


router = APIRouter(tags=["video-preprocessing"])


# ── GET /video-preprocessing ────────────────────────────────────────────────
# 列表端点必须在 ``/{job_id}`` 之前声明；虽然 ``{job_id}: UUID`` 的类型约束让
# FastAPI 不会错判空路径，但保持 "更具体路径在前" 的习惯有助于未来扩展
# （例如新增 /video-preprocessing/stats 等子资源时避免歧义）。

@router.get(
    "/video-preprocessing",
    response_model=SuccessEnvelope[list[PreprocessingJobListItem]],
    summary="List preprocessing jobs with pagination and status filter",
)
async def list_preprocessing_jobs(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数，最大 100"),
    status: Optional[str] = Query(
        None,
        description="按 job 状态筛选: running / success / failed / superseded",
    ),
    cos_object_key: Optional[str] = Query(
        None,
        description="按原视频 COS key 精确匹配（可选）",
        max_length=1024,
    ),
    sort_by: str = Query(
        "started_at",
        description="排序字段: started_at / completed_at / created_at",
    ),
    order: str = Query("desc", description="排序方向: asc / desc"),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[PreprocessingJobListItem]]:
    """Return a paginated page of ``video_preprocessing_jobs`` rows.

    Feature-016 预处理任务独立于 ``analysis_tasks``，因此不会出现在
    ``GET /api/v1/tasks`` 列表里。运维通过本端点可按时间倒序浏览所有
    预处理 job，并按状态 / cos_object_key 过滤。
    """
    # ── 枚举白名单校验（项目规则 1：非法枚举 → 400 INVALID_ENUM_VALUE + details 含合法取值） ──
    _ALLOWED_STATUS = ("running", "success", "failed", "superseded")
    _ALLOWED_SORT_BY = ("started_at", "completed_at", "created_at")
    _ALLOWED_ORDER = ("asc", "desc")
    if status is not None and status not in _ALLOWED_STATUS:
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            message=f"status={status!r} 非法",
            details={"field": "status", "allowed_values": list(_ALLOWED_STATUS)},
        )
    if sort_by not in _ALLOWED_SORT_BY:
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            message=f"sort_by={sort_by!r} 非法",
            details={"field": "sort_by", "allowed_values": list(_ALLOWED_SORT_BY)},
        )
    if order not in _ALLOWED_ORDER:
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            message=f"order={order!r} 非法",
            details={"field": "order", "allowed_values": list(_ALLOWED_ORDER)},
        )

    rows, total = await _preprocessing_service.list_jobs(
        db,
        page=page,
        page_size=page_size,
        status=status,
        cos_object_key=cos_object_key,
        sort_by=sort_by,
        order=order,
    )

    items = [
        PreprocessingJobListItem(
            job_id=row.id,
            cos_object_key=row.cos_object_key,
            status=row.status,
            force=row.force,
            started_at=row.started_at,
            completed_at=row.completed_at,
            duration_ms=row.duration_ms,
            segment_count=row.segment_count,
            has_audio=row.has_audio,
            error_message=row.error_message,
        )
        for row in rows
    ]
    return page_envelope(items, page=page, page_size=page_size, total=total)


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
