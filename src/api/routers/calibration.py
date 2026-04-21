"""Calibration router — multi-coach tech parameter comparison (Feature 006).

Endpoints:
  GET /calibration/tech-points     → compare tech params across coaches (action_type + dimension required)
  GET /calibration/teaching-tips   → compare teaching tips across coaches (action_type + tech_phase required)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.coach import (
    CoachTechPointEntry,
    CoachTipGroup,
    TeachingTipCalibrationView,
    TechPointCalibrationView,
)
from src.db.session import get_db
from src.models.analysis_task import AnalysisTask
from src.models.coach import Coach
from src.models.expert_tech_point import ExpertTechPoint
from src.models.teaching_tip import TeachingTip

logger = logging.getLogger(__name__)
router = APIRouter(tags=["calibration"])


# ── GET /calibration/tech-points ──────────────────────────────────────────────

@router.get("/calibration/tech-points", response_model=TechPointCalibrationView)
async def calibrate_tech_points(
    action_type: str = Query(..., description="动作类型，如 forehand_topspin"),
    dimension: str = Query(..., description="技术维度，如 elbow_angle"),
    db: AsyncSession = Depends(get_db),
) -> TechPointCalibrationView:
    """Return multi-coach comparison for a specific action_type + dimension."""
    # Query: expert_tech_points JOIN analysis_tasks JOIN coaches
    stmt = (
        select(ExpertTechPoint, Coach)
        .join(AnalysisTask, ExpertTechPoint.source_video_id == AnalysisTask.id)
        .outerjoin(Coach, AnalysisTask.coach_id == Coach.id)
        .where(
            ExpertTechPoint.action_type == action_type,
            ExpertTechPoint.dimension == dimension,
            AnalysisTask.coach_id.isnot(None),
        )
        .order_by(Coach.name)
    )
    result = await db.execute(stmt)
    rows = result.all()

    # Group by coach
    coach_map: dict = {}
    for ep, coach in rows:
        cid = str(coach.id)
        if cid not in coach_map:
            coach_map[cid] = {
                "coach_id": coach.id,
                "coach_name": coach.name,
                "param_mins": [],
                "param_ideals": [],
                "param_maxes": [],
                "unit": ep.unit or "",
                "confidences": [],
            }
        coach_map[cid]["param_mins"].append(ep.param_min)
        coach_map[cid]["param_ideals"].append(ep.param_ideal)
        coach_map[cid]["param_maxes"].append(ep.param_max)
        coach_map[cid]["confidences"].append(ep.extraction_confidence or 0.0)

    entries: list[CoachTechPointEntry] = []
    for data in coach_map.values():
        count = len(data["param_mins"])
        entries.append(
            CoachTechPointEntry(
                coach_id=data["coach_id"],
                coach_name=data["coach_name"],
                param_min=sum(data["param_mins"]) / count,
                param_ideal=sum(data["param_ideals"]) / count,
                param_max=sum(data["param_maxes"]) / count,
                unit=data["unit"],
                extraction_confidence=sum(data["confidences"]) / count,
                source_count=count,
            )
        )

    logger.info(
        "calibration tech-points action_type=%s dimension=%s coaches=%d",
        action_type, dimension, len(entries),
    )
    return TechPointCalibrationView(
        action_type=action_type,
        dimension=dimension,
        coaches=entries,
    )


# ── GET /calibration/teaching-tips ────────────────────────────────────────────

@router.get("/calibration/teaching-tips", response_model=TeachingTipCalibrationView)
async def calibrate_teaching_tips(
    action_type: str = Query(..., description="动作类型，如 forehand_topspin"),
    tech_phase: str = Query(..., description="技术阶段，如 contact"),
    db: AsyncSession = Depends(get_db),
) -> TeachingTipCalibrationView:
    """Return multi-coach teaching tip comparison grouped by coach."""
    stmt = (
        select(TeachingTip, Coach)
        .join(AnalysisTask, TeachingTip.task_id == AnalysisTask.id)
        .outerjoin(Coach, AnalysisTask.coach_id == Coach.id)
        .where(
            TeachingTip.action_type == action_type,
            TeachingTip.tech_phase == tech_phase,
            AnalysisTask.coach_id.isnot(None),
        )
        .order_by(Coach.name)
    )
    result = await db.execute(stmt)
    rows = result.all()

    # Group by coach
    coach_map: dict = {}
    for tip, coach in rows:
        cid = str(coach.id)
        if cid not in coach_map:
            coach_map[cid] = {
                "coach_id": coach.id,
                "coach_name": coach.name,
                "tips": [],
            }
        coach_map[cid]["tips"].append(tip.tip_text)

    groups: list[CoachTipGroup] = [
        CoachTipGroup(
            coach_id=data["coach_id"],
            coach_name=data["coach_name"],
            tips=data["tips"],
        )
        for data in coach_map.values()
    ]

    logger.info(
        "calibration teaching-tips action_type=%s tech_phase=%s coaches=%d",
        action_type, tech_phase, len(groups),
    )
    return TeachingTipCalibrationView(
        action_type=action_type,
        tech_phase=tech_phase,
        coaches=groups,
    )
