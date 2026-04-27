"""Coaches router — CRUD for Coach entities (Feature 006).

Endpoints:
  POST   /coaches                    → create coach (201)
  GET    /coaches                    → list active coaches
  GET    /coaches/{coach_id}         → get single coach
  PATCH  /coaches/{coach_id}         → update coach name/bio
  DELETE /coaches/{coach_id}         → soft-delete (204)
  PATCH  /tasks/{task_id}/coach      → associate coach to task

Feature-017: 响应体统一迁移至 ``SuccessEnvelope``；``HTTPException`` 改为 ``AppException``
（章程 v1.4.0 原则 IX）。``PATCH /tasks/{task_id}/coach`` 暂留此处，后续阶段 5 T050
搬迁到 tasks.py（仅跨文件剪切，业务逻辑不动）。
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.coach import (
    CoachCreate,
    CoachResponse,
    CoachUpdate,
    TaskCoachResponse,
    TaskCoachUpdate,
)
from src.api.schemas.envelope import SuccessEnvelope, ok, page as page_envelope
from src.db.session import get_db
from src.models.analysis_task import AnalysisTask
from src.models.coach import Coach

logger = logging.getLogger(__name__)
router = APIRouter(tags=["coaches"])


# ── POST /coaches ─────────────────────────────────────────────────────────────

@router.post("/coaches", status_code=201, response_model=SuccessEnvelope[CoachResponse])
async def create_coach(
    body: CoachCreate,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[CoachResponse]:
    """Create a new coach. Name must be globally unique."""
    coach = Coach(name=body.name, bio=body.bio)
    db.add(coach)
    try:
        await db.commit()
        await db.refresh(coach)
    except IntegrityError:
        await db.rollback()
        raise AppException(
            ErrorCode.COACH_NAME_CONFLICT,
            message=f"教练名称 '{body.name}' 已存在",
            details={"name": body.name},
        )
    logger.info("coach created id=%s name=%s", coach.id, coach.name)
    return ok(CoachResponse.model_validate(coach))


# ── GET /coaches ──────────────────────────────────────────────────────────────

@router.get("/coaches", response_model=SuccessEnvelope[list[CoachResponse]])
async def list_coaches(
    include_inactive: bool = False,
    page_num: int = Query(1, ge=1, alias="page"),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[CoachResponse]]:
    """List coaches. By default only returns active coaches.

    Feature-017 阶段 5 T054：统一 ``page/page_size`` 分页参数（默认 20、最大 100）；
    越界由 FastAPI 422 + VALIDATION_FAILED 自动拦截。
    """
    stmt = select(Coach)
    count_stmt = select(func.count()).select_from(Coach)
    if not include_inactive:
        stmt = stmt.where(Coach.is_active.is_(True))
        count_stmt = count_stmt.where(Coach.is_active.is_(True))

    total_result = await db.execute(count_stmt)
    total = int(total_result.scalar() or 0)

    offset = (page_num - 1) * page_size
    stmt = stmt.order_by(Coach.created_at).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    coaches = result.scalars().all()
    items = [CoachResponse.model_validate(c) for c in coaches]
    return page_envelope(items, page=page_num, page_size=page_size, total=total)


# ── GET /coaches/{coach_id} ───────────────────────────────────────────────────

@router.get("/coaches/{coach_id}", response_model=SuccessEnvelope[CoachResponse])
async def get_coach(
    coach_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[CoachResponse]:
    """Get a single coach by ID."""
    result = await db.execute(select(Coach).where(Coach.id == coach_id))
    coach = result.scalar_one_or_none()
    if coach is None:
        raise AppException(
            ErrorCode.COACH_NOT_FOUND,
            details={"coach_id": str(coach_id)},
        )
    return ok(CoachResponse.model_validate(coach))


# ── PATCH /coaches/{coach_id} ─────────────────────────────────────────────────

@router.patch("/coaches/{coach_id}", response_model=SuccessEnvelope[CoachResponse])
async def update_coach(
    coach_id: uuid.UUID,
    body: CoachUpdate,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[CoachResponse]:
    """Update coach name and/or bio."""
    result = await db.execute(select(Coach).where(Coach.id == coach_id))
    coach = result.scalar_one_or_none()
    if coach is None:
        raise AppException(
            ErrorCode.COACH_NOT_FOUND,
            details={"coach_id": str(coach_id)},
        )
    if body.name is not None:
        coach.name = body.name
    if body.bio is not None:
        coach.bio = body.bio
    try:
        await db.commit()
        await db.refresh(coach)
    except IntegrityError:
        await db.rollback()
        raise AppException(
            ErrorCode.COACH_NAME_CONFLICT,
            message=f"教练名称 '{body.name}' 已被占用",
            details={"name": body.name},
        )
    logger.info("coach updated id=%s name=%s", coach.id, coach.name)
    return ok(CoachResponse.model_validate(coach))


# ── DELETE /coaches/{coach_id} ────────────────────────────────────────────────

@router.delete("/coaches/{coach_id}", status_code=204, response_model=None)
async def soft_delete_coach(
    coach_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a coach (sets is_active=False). Historical task data is preserved.

    204 响应按约定不携带响应体（信封不适用）。
    """
    result = await db.execute(select(Coach).where(Coach.id == coach_id))
    coach = result.scalar_one_or_none()
    if coach is None:
        raise AppException(
            ErrorCode.COACH_NOT_FOUND,
            details={"coach_id": str(coach_id)},
        )
    if not coach.is_active:
        raise AppException(ErrorCode.COACH_ALREADY_INACTIVE)
    coach.is_active = False
    await db.commit()
    logger.info("coach soft-deleted id=%s name=%s", coach.id, coach.name)


# ── PATCH /tasks/{task_id}/coach ──────────────────────────────────────────────

@router.patch("/tasks/{task_id}/coach", response_model=SuccessEnvelope[TaskCoachResponse])
async def assign_coach_to_task(
    task_id: uuid.UUID,
    body: TaskCoachUpdate,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[TaskCoachResponse]:
    """Assign (or remove) a coach for an expert video task."""
    task_result = await db.execute(
        select(AnalysisTask).where(
            AnalysisTask.id == task_id,
            AnalysisTask.deleted_at.is_(None),
        )
    )
    task = task_result.scalar_one_or_none()
    if task is None:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"task_id": str(task_id)},
        )

    coach_name: str | None = None
    if body.coach_id is not None:
        coach_result = await db.execute(
            select(Coach).where(Coach.id == body.coach_id)
        )
        coach = coach_result.scalar_one_or_none()
        if coach is None:
            raise AppException(
                ErrorCode.COACH_NOT_FOUND,
                details={"coach_id": str(body.coach_id)},
            )
        if not coach.is_active:
            raise AppException(
                ErrorCode.COACH_INACTIVE,
                message="无法关联已停用的教练",
                details={"coach_id": str(body.coach_id)},
            )
        coach_name = coach.name

    task.coach_id = body.coach_id
    await db.commit()
    logger.info(
        "task coach assigned task_id=%s coach_id=%s", task_id, body.coach_id
    )
    return ok(TaskCoachResponse(
        task_id=task_id,
        coach_id=body.coach_id,
        coach_name=coach_name,
    ))
