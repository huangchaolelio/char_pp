"""Deviation analyzer service — computes deviation reports for athlete motion analyses.

For each matched (AthleteMotionAnalysis, ExpertTechPoint) pair:
  - deviation_value = measured - ideal
  - deviation_direction:
      measured > param_max  → above
      measured < param_min  → below
      otherwise             → none
  - impact_score = abs(deviation_value) / (param_max - param_min), clamped to [0, 1]
    Falls back to abs(deviation_value) if param range is zero.
  - is_low_confidence = True if overall_confidence < 0.7

Stability aggregation (T034):
  compute_stability(task_ids, action_type, dimension) scans historical records
  for the same athlete (via task_ids belonging to the same athlete).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.athlete_motion_analysis import AthleteActionType, AthleteMotionAnalysis
from src.models.deviation_report import DeviationDirection, DeviationReport
from src.models.expert_tech_point import ExpertTechPoint

logger = logging.getLogger(__name__)

_LOW_CONFIDENCE_THRESHOLD = 0.7
_STABILITY_MIN_SAMPLES = 3
_STABILITY_DEVIATION_RATE = 0.70


@dataclass
class DeviationInput:
    """Holds a single measured dimension value for comparison."""
    dimension: str
    measured_value: float
    unit: str
    confidence: float


def _compute_direction(
    measured: float,
    param_min: float,
    param_max: float,
) -> DeviationDirection:
    if measured > param_max:
        return DeviationDirection.above
    if measured < param_min:
        return DeviationDirection.below
    return DeviationDirection.none


def _compute_impact(
    deviation_value: float,
    param_min: float,
    param_max: float,
) -> float:
    """Normalize abs(deviation) to [0, 1] relative to param range."""
    param_range = param_max - param_min
    if param_range <= 1e-9:
        # Degenerate range: use raw abs deviation clamped to 1
        return min(1.0, abs(deviation_value))
    return min(1.0, abs(deviation_value) / param_range)


async def analyze_deviations(
    session: AsyncSession,
    motion_analysis: AthleteMotionAnalysis,
    expert_points: list[ExpertTechPoint],
) -> list[DeviationReport]:
    """Compute and persist DeviationReport records for one motion analysis.

    Args:
        session: Active async DB session (caller manages commit).
        motion_analysis: The AthleteMotionAnalysis record (already persisted).
        expert_points: List of ExpertTechPoint records for the matching action_type
                       in the active KB version.

    Returns:
        List of newly created (but not yet committed) DeviationReport records.
    """
    reports: list[DeviationReport] = []

    # Index expert points by dimension for O(1) lookup
    expert_by_dim: dict[str, ExpertTechPoint] = {p.dimension: p for p in expert_points}

    measured_params: Optional[dict] = motion_analysis.measured_params or {}

    for dimension, expert_point in expert_by_dim.items():
        dim_data = measured_params.get(dimension)
        if dim_data is None:
            logger.debug(
                "No measured value for dimension '%s' in analysis %s — skipping",
                dimension, motion_analysis.id,
            )
            continue

        measured_value: float = float(dim_data.get("value", 0.0))
        dim_confidence: float = float(
            dim_data.get("confidence", motion_analysis.overall_confidence)
        )

        deviation_value = measured_value - expert_point.param_ideal
        direction = _compute_direction(
            measured_value, expert_point.param_min, expert_point.param_max
        )
        impact = _compute_impact(deviation_value, expert_point.param_min, expert_point.param_max)
        is_low = dim_confidence < _LOW_CONFIDENCE_THRESHOLD

        report = DeviationReport(
            analysis_id=motion_analysis.id,
            expert_point_id=expert_point.id,
            dimension=dimension,
            measured_value=measured_value,
            ideal_value=expert_point.param_ideal,
            deviation_value=deviation_value,
            deviation_direction=direction,
            confidence=dim_confidence,
            is_low_confidence=is_low,
            is_stable_deviation=None,  # computed via compute_stability later
            impact_score=impact,
        )
        session.add(report)
        reports.append(report)

    await session.flush()  # assign PKs without committing
    logger.info(
        "Created %d deviation reports for analysis %s",
        len(reports), motion_analysis.id,
    )
    return reports


async def compute_stability(
    session: AsyncSession,
    analysis_ids: list[uuid.UUID],
    action_type: str,
    dimension: str,
) -> Optional[bool]:
    """Determine whether a deviation is stable across multiple analyses.

    Args:
        session: Active async DB session.
        analysis_ids: IDs of AthleteMotionAnalysis records belonging to the same athlete
                      (same athlete_id or derived from related task_ids).
        action_type: The action type to filter on (e.g. 'forehand_topspin').
        dimension: The dimension to evaluate (e.g. 'elbow_angle').

    Returns:
        True  — ≥3 samples and ≥70% show a deviation (direction ≠ none)
        False — ≥3 samples but < 70% show deviation
        None  — < 3 samples (insufficient data)
    """
    if not analysis_ids:
        return None

    # Fetch all deviation reports for the matching analysis_ids + dimension
    stmt = (
        select(DeviationReport)
        .join(AthleteMotionAnalysis, DeviationReport.analysis_id == AthleteMotionAnalysis.id)
        .where(
            AthleteMotionAnalysis.id.in_(analysis_ids),
            AthleteMotionAnalysis.action_type == AthleteActionType(action_type),
            DeviationReport.dimension == dimension,
        )
    )
    result = await session.execute(stmt)
    reports = result.scalars().all()

    total = len(reports)
    if total < _STABILITY_MIN_SAMPLES:
        return None  # insufficient data

    deviated = sum(
        1 for r in reports if r.deviation_direction != DeviationDirection.none
    )
    rate = deviated / total
    is_stable = rate >= _STABILITY_DEVIATION_RATE
    logger.debug(
        "Stability check: action=%s dim=%s samples=%d deviated=%d rate=%.2f stable=%s",
        action_type, dimension, total, deviated, rate, is_stable,
    )
    return is_stable
