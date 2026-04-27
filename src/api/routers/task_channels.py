"""Task-channels status router (Feature 013 FR-018).

Read-only endpoints for operators/monitoring:
  * ``GET /api/v1/task-channels`` — all three channels' live snapshots.
  * ``GET /api/v1/task-channels/{task_type}`` — one channel by type.

Admin mutation of channels lives in :mod:`src.api.routers.admin`.

Feature-017: 响应体统一迁移至 ``SuccessEnvelope`` / ``ErrorEnvelope`` 信封
（章程 v1.4.0 原则 IX）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.envelope import SuccessEnvelope, ok, page as page_envelope
from src.api.schemas.task_submit import ChannelSnapshot
from src.db.session import get_db
from src.models.analysis_task import TaskType
from src.services.task_channel_service import TaskChannelService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["task-channels"])


def _snapshot_to_schema(snap) -> ChannelSnapshot:
    return ChannelSnapshot(
        task_type=snap.task_type.value,
        queue_capacity=snap.queue_capacity,
        concurrency=snap.concurrency,
        current_pending=snap.current_pending,
        current_processing=snap.current_processing,
        remaining_slots=snap.remaining_slots,
        enabled=snap.enabled,
        recent_completion_rate_per_min=snap.recent_completion_rate_per_min,
    )


@router.get(
    "/task-channels",
    status_code=200,
    summary="List live snapshots for all task channels",
    response_model=SuccessEnvelope[list[ChannelSnapshot]],
)
async def list_channels(
    page_num: int = Query(1, ge=1, alias="page"),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[ChannelSnapshot]]:
    """Return all channel snapshots in enum order.

    Feature-017 阶段 5 T054：统一 ``page/page_size`` 分页参数（默认 20、最大 100）。
    频道为枚举型资源（TaskType 枚举限有），通常不需分页，但参数保持一致以通过
    命名规范 linter。
    """
    svc = TaskChannelService()
    snapshots: list[ChannelSnapshot] = []
    for tt in TaskType:
        snap = await svc.get_snapshot(db, tt)
        snapshots.append(_snapshot_to_schema(snap))
    total = len(snapshots)
    offset = (page_num - 1) * page_size
    sliced = snapshots[offset : offset + page_size]
    return page_envelope(sliced, page=page_num, page_size=page_size, total=total)


@router.get(
    "/task-channels/{task_type}",
    response_model=SuccessEnvelope[ChannelSnapshot],
    status_code=200,
    summary="Get a single channel's live snapshot",
)
async def get_channel(
    task_type: str = Path(
        ...,
        description="video_classification | kb_extraction | athlete_diagnosis",
    ),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[ChannelSnapshot]:
    try:
        tt = TaskType(task_type)
    except ValueError:
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            message=f"unknown task_type {task_type!r}",
            details={
                "field": "task_type",
                "value": task_type,
                "allowed": [t.value for t in TaskType],
            },
        )

    svc = TaskChannelService()
    snap = await svc.get_snapshot(db, tt)
    return ok(_snapshot_to_schema(snap))
