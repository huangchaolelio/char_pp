"""Coaches router — CRUD for Coach entities (Feature 006).

Endpoints:
  POST   /coaches                    → create coach (201)
  GET    /coaches                    → list active coaches
  GET    /coaches/{coach_id}         → get single coach
  PATCH  /coaches/{coach_id}         → update coach name/bio
  DELETE /coaches/{coach_id}         → soft-delete (204)
  PATCH  /tasks/{task_id}/coach      → associate coach to task
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.coach import (
    CoachCreate,
    CoachResponse,
    CoachUpdate,
    TaskCoachResponse,
    TaskCoachUpdate,
)
from src.db.session import get_db
from src.models.analysis_task import AnalysisTask
from src.models.coach import Coach

logger = logging.getLogger(__name__)
router = APIRouter(tags=["coaches"])


# ── POST /coaches ─────────────────────────────────────────────────────────────

@router.post("/coaches", status_code=201, response_model=CoachResponse)
async def create_coach(
    body: CoachCreate,
    db: AsyncSession = Depends(get_db),
) -> CoachResponse:
    """Create a new coach. Name must be globally unique."""
    coach = Coach(name=body.name, bio=body.bio)
    db.add(coach)
    try:
        await db.commit()
        await db.refresh(coach)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COACH_NAME_CONFLICT",
                "message": f"教练名称 '{body.name}' 已存在",
                "details": {"name": body.name},
            },
        )
    logger.info("coach created id=%s name=%s", coach.id, coach.name)
    return CoachResponse.model_validate(coach)


# ── GET /coaches ──────────────────────────────────────────────────────────────

@router.get("/coaches", response_model=list[CoachResponse])
async def list_coaches(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[CoachResponse]:
    """List coaches. By default only returns active coaches."""
    stmt = select(Coach)
    if not include_inactive:
        stmt = stmt.where(Coach.is_active.is_(True))
    stmt = stmt.order_by(Coach.created_at)
    result = await db.execute(stmt)
    coaches = result.scalars().all()
    return [CoachResponse.model_validate(c) for c in coaches]


# ── GET /coaches/{coach_id} ───────────────────────────────────────────────────

@router.get("/coaches/{coach_id}", response_model=CoachResponse)
async def get_coach(
    coach_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CoachResponse:
    """Get a single coach by ID."""
    result = await db.execute(select(Coach).where(Coach.id == coach_id))
    coach = result.scalar_one_or_none()
    if coach is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "COACH_NOT_FOUND", "message": "教练不存在"},
        )
    return CoachResponse.model_validate(coach)


# ── PATCH /coaches/{coach_id} ─────────────────────────────────────────────────

@router.patch("/coaches/{coach_id}", response_model=CoachResponse)
async def update_coach(
    coach_id: uuid.UUID,
    body: CoachUpdate,
    db: AsyncSession = Depends(get_db),
) -> CoachResponse:
    """Update coach name and/or bio."""
    result = await db.execute(select(Coach).where(Coach.id == coach_id))
    coach = result.scalar_one_or_none()
    if coach is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "COACH_NOT_FOUND", "message": "教练不存在"},
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
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COACH_NAME_CONFLICT",
                "message": f"教练名称 '{body.name}' 已被占用",
                "details": {"name": body.name},
            },
        )
    logger.info("coach updated id=%s name=%s", coach.id, coach.name)
    return CoachResponse.model_validate(coach)


# ── DELETE /coaches/{coach_id} ────────────────────────────────────────────────

@router.delete("/coaches/{coach_id}", status_code=204, response_model=None)
async def soft_delete_coach(
    coach_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a coach (sets is_active=False). Historical task data is preserved."""
    result = await db.execute(select(Coach).where(Coach.id == coach_id))
    coach = result.scalar_one_or_none()
    if coach is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "COACH_NOT_FOUND", "message": "教练不存在"},
        )
    if not coach.is_active:
        raise HTTPException(
            status_code=409,
            detail={"code": "COACH_ALREADY_INACTIVE", "message": "教练已处于停用状态"},
        )
    coach.is_active = False
    await db.commit()
    logger.info("coach soft-deleted id=%s name=%s", coach.id, coach.name)


# ── PATCH /tasks/{task_id}/coach ──────────────────────────────────────────────

@router.patch("/tasks/{task_id}/coach", response_model=TaskCoachResponse)
async def assign_coach_to_task(
    task_id: uuid.UUID,
    body: TaskCoachUpdate,
    db: AsyncSession = Depends(get_db),
) -> TaskCoachResponse:
    """Assign (or remove) a coach for an expert video task."""
    task_result = await db.execute(
        select(AnalysisTask).where(
            AnalysisTask.id == task_id,
            AnalysisTask.deleted_at.is_(None),
        )
    )
    task = task_result.scalar_one_or_none()
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"},
        )

    coach_name: Optional[str] = None
    if body.coach_id is not None:
        coach_result = await db.execute(
            select(Coach).where(Coach.id == body.coach_id)
        )
        coach = coach_result.scalar_one_or_none()
        if coach is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "COACH_NOT_FOUND", "message": "教练不存在"},
            )
        if not coach.is_active:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "COACH_INACTIVE",
                    "message": "无法关联已停用的教练",
                    "details": {"coach_id": str(body.coach_id)},
                },
            )
        coach_name = coach.name

    task.coach_id = body.coach_id
    await db.commit()
    logger.info(
        "task coach assigned task_id=%s coach_id=%s", task_id, body.coach_id
    )
    return TaskCoachResponse(
        task_id=task_id,
        coach_id=body.coach_id,
        coach_name=coach_name,
    )
