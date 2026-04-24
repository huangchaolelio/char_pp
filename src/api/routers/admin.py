"""Admin router — privileged operations guarded by ``settings.admin_reset_token``.

Endpoints:
  * ``POST /api/v1/admin/reset-task-pipeline`` — Feature 013 US4 data reset
    (TRUNCATE task-related tables, preserve core assets).
  * ``PATCH /api/v1/admin/channels/{task_type}`` — update a channel's
    ``queue_capacity`` / ``concurrency`` / ``enabled`` at runtime (30s TTL
    cache invalidation handled by the service).

All admin routes:
  * Require ``confirmation_token`` (body) or ``X-Admin-Token`` header matching
    ``settings.admin_reset_token``.
  * Emit an ``X-Admin-Operation: true`` response header for audit logging by
    reverse proxies.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.task_submit import (
    ChannelConfigPatch,
    ChannelSnapshot,
    DataResetRequest,
    ResetReport,
)
from src.config import get_settings
from src.db.session import get_db
from src.models.analysis_task import TaskType
from src.services.task_channel_service import TaskChannelService
from src.services.task_reset_service import TaskResetService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


def _verify_admin_token(provided: str | None) -> None:
    """Constant-time compare against ``settings.admin_reset_token``.

    Raises 403 ``ADMIN_TOKEN_INVALID`` when the token is missing, empty, or
    mismatched. Raises 500 when the server has no token configured (fail-safe
    closed — never allow reset on an unconfigured server).
    """
    settings = get_settings()
    expected = settings.admin_reset_token or ""
    if not expected:
        logger.error("admin endpoint called but ADMIN_RESET_TOKEN is not configured")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "ADMIN_TOKEN_NOT_CONFIGURED",
                    "message": "server missing ADMIN_RESET_TOKEN",
                }
            },
        )
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "ADMIN_TOKEN_INVALID",
                    "message": "confirmation token missing or mismatched",
                }
            },
        )


@router.post(
    "/admin/reset-task-pipeline",
    response_model=ResetReport,
    status_code=200,
    summary="Reset Feature-013 task-pipeline data (destructive)",
)
async def reset_task_pipeline(
    body: DataResetRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ResetReport:
    """Truncate task-related tables and delete draft KB versions.

    Core assets (coaches, classifications, tech standards, published KBs,
    skills, reference videos) are preserved — their counts are returned in
    ``preserved_counts`` for audit.
    """
    _verify_admin_token(body.confirmation_token)
    response.headers["X-Admin-Operation"] = "true"

    svc = TaskResetService()
    report_data = await svc.reset(session=db, dry_run=body.dry_run)
    return ResetReport(
        reset_at=report_data.reset_at,
        dry_run=report_data.dry_run,
        deleted_counts=report_data.deleted_counts,
        preserved_counts=report_data.preserved_counts,
        duration_ms=report_data.duration_ms,
    )


@router.patch(
    "/admin/channels/{task_type}",
    response_model=ChannelSnapshot,
    status_code=200,
    summary="Update a task channel's capacity / concurrency / enabled flag",
)
async def patch_channel_config(
    request: Request,
    response: Response,
    body: ChannelConfigPatch,
    task_type: str = Path(..., description="video_classification | kb_extraction | athlete_diagnosis"),
    db: AsyncSession = Depends(get_db),
) -> ChannelSnapshot:
    """Update runtime channel config; 30s TTL cache auto-invalidates."""
    token = request.headers.get("X-Admin-Token")
    _verify_admin_token(token)
    response.headers["X-Admin-Operation"] = "true"

    try:
        tt = TaskType(task_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_INPUT",
                    "message": f"unknown task_type {task_type!r}",
                }
            },
        )

    svc = TaskChannelService()
    try:
        updated = await svc.update_config(
            session=db,
            task_type=tt,
            queue_capacity=body.queue_capacity,
            concurrency=body.concurrency,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_INPUT", "message": str(exc)}},
        ) from exc

    snapshot = await svc.get_snapshot(db, tt)
    return ChannelSnapshot(
        task_type=snapshot.task_type.value,
        queue_capacity=snapshot.queue_capacity,
        concurrency=snapshot.concurrency,
        current_pending=snapshot.current_pending,
        current_processing=snapshot.current_processing,
        remaining_slots=snapshot.remaining_slots,
        enabled=snapshot.enabled,
        recent_completion_rate_per_min=snapshot.recent_completion_rate_per_min,
    )
