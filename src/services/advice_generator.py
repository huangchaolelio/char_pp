"""Advice generator service — generates actionable coaching advice from deviation reports.

For each DeviationReport where deviation_direction ≠ none, one CoachingAdvice is generated:
  - deviation_description: e.g. "正手拉球肘部角度偏大 32.5°"
  - improvement_target: references ExpertTechPoint param_min/ideal/param_max
  - improvement_method: actionable training suggestion (rule-based templates)
  - reliability_level: high (confidence ≥ 0.7) or low (< 0.7)
  - reliability_note: required for low reliability
  - impact_score: inherited from DeviationReport.impact_score

Output is sorted by impact_score DESC.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.coaching_advice import CoachingAdvice, ReliabilityLevel
from src.models.deviation_report import DeviationDirection, DeviationReport
from src.models.expert_tech_point import ExpertTechPoint

logger = logging.getLogger(__name__)

_LOW_CONFIDENCE_THRESHOLD = 0.7

# ── Dimension display names ────────────────────────────────────────────────────

_DIMENSION_NAMES: dict[str, str] = {
    "elbow_angle": "肘部角度",
    "swing_trajectory": "挥拍轨迹",
    "contact_timing": "击球时机",
    "weight_transfer": "重心转移",
}

_ACTION_NAMES: dict[str, str] = {
    "forehand_topspin": "正手拉球",
    "backhand_push": "反手拨球",
}

# ── Training suggestion templates ─────────────────────────────────────────────

_IMPROVEMENT_TEMPLATES: dict[str, dict[str, str]] = {
    "elbow_angle": {
        "above": (
            "在挥拍准备阶段，主动收缩大臂，将肘部弯曲角度控制在标准范围内。"
            "可通过慢动作镜前练习或弹力带辅助训练，强化肘部折叠意识。"
        ),
        "below": (
            "适当放松大臂，在引拍时让肘部自然展开至标准角度。"
            "可通过对墙慢推练习，感受肘部展开的完整行程。"
        ),
    },
    "swing_trajectory": {
        "above": (
            "减少多余的手腕和小臂动作，使挥拍路径更直接、紧凑。"
            "可用固定支架辅助练习，限制过大的弧线运动。"
        ),
        "below": (
            "增加完整的引拍幅度，使挥拍轨迹覆盖足够的弧线距离。"
            "可对镜模仿教练示范的挥拍路径，逐步扩大动作幅度。"
        ),
    },
    "contact_timing": {
        "above": (
            "提前准备击球时机，在球弹起的上升期即发力触球。"
            "多球练习中专注于更早触球的时间节点，逐步建立肌肉记忆。"
        ),
        "below": (
            "适当延迟触球时机，等待球弹至合适高度再发力。"
            "可通过节拍器配合多球训练，找到最佳击球节奏。"
        ),
    },
    "weight_transfer": {
        "above": (
            "控制重心侧移幅度，避免过大的横向晃动影响平衡和还原速度。"
            "可进行单腿平衡站立练习，增强核心稳定性。"
        ),
        "below": (
            "加强重心转移训练，在挥拍过程中主动将重心从后脚蹬地转移至前脚。"
            "可通过步伐配合练习，将腿部蹬地力量传导至上肢动作。"
        ),
    },
}

_DEFAULT_IMPROVEMENT = "请针对该维度偏差进行专项训练，建议请专业教练进行针对性指导。"


def _direction_word(direction: DeviationDirection) -> str:
    return "偏大" if direction == DeviationDirection.above else "偏小"


def _format_deviation_description(
    action_type: str,
    dimension: str,
    deviation_value: float,
    direction: DeviationDirection,
    unit: str,
) -> str:
    action_name = _ACTION_NAMES.get(action_type, action_type)
    dim_name = _DIMENSION_NAMES.get(dimension, dimension)
    direction_word = _direction_word(direction)
    abs_dev = abs(deviation_value)
    if unit in ("°", "ms"):
        return f"{action_name}{dim_name}{direction_word} {abs_dev:.1f}{unit}"
    else:
        return f"{action_name}{dim_name}{direction_word} {abs_dev:.3f}（{unit}）"


def _format_improvement_target(expert_point: ExpertTechPoint) -> str:
    dim_name = _DIMENSION_NAMES.get(expert_point.dimension, expert_point.dimension)
    unit = expert_point.unit
    return (
        f"将{dim_name}控制在 {expert_point.param_min:.2f}{unit}"
        f"～{expert_point.param_max:.2f}{unit} 范围内"
        f"（理想值 {expert_point.param_ideal:.2f}{unit}）"
    )


def _get_improvement_method(dimension: str, direction: DeviationDirection) -> str:
    templates = _IMPROVEMENT_TEMPLATES.get(dimension)
    if templates is None:
        return _DEFAULT_IMPROVEMENT
    direction_key = "above" if direction == DeviationDirection.above else "below"
    return templates.get(direction_key, _DEFAULT_IMPROVEMENT)


async def generate_advice(
    session: AsyncSession,
    task_id: uuid.UUID,
    deviation_reports: list[DeviationReport],
    expert_points_by_id: dict[uuid.UUID, ExpertTechPoint],
    action_type: str,
) -> list[CoachingAdvice]:
    """Generate and persist CoachingAdvice records for a set of deviation reports.

    Args:
        session: Active async DB session (caller manages commit).
        task_id: The AnalysisTask.id this advice belongs to.
        deviation_reports: List of DeviationReport records (already flushed).
        expert_points_by_id: Mapping from ExpertTechPoint.id → ExpertTechPoint.
        action_type: The action type string (e.g. 'forehand_topspin').

    Returns:
        List of CoachingAdvice records sorted by impact_score DESC.
    """
    advice_list: list[CoachingAdvice] = []

    for report in deviation_reports:
        # Only generate advice for actual deviations
        if report.deviation_direction == DeviationDirection.none:
            continue

        expert_point = expert_points_by_id.get(report.expert_point_id)
        if expert_point is None:
            logger.warning(
                "ExpertTechPoint %s not found for deviation %s — skipping advice",
                report.expert_point_id, report.id,
            )
            continue

        is_low = report.confidence < _LOW_CONFIDENCE_THRESHOLD
        reliability_level = ReliabilityLevel.low if is_low else ReliabilityLevel.high
        reliability_note: Optional[str] = (
            f"该建议基于置信度 {report.confidence:.2f} 的分析结果（低于 0.7 阈值），仅供参考，"
            "建议结合专业教练的现场观察综合判断。"
            if is_low else None
        )

        advice = CoachingAdvice(
            deviation_id=report.id,
            task_id=task_id,
            deviation_description=_format_deviation_description(
                action_type=action_type,
                dimension=report.dimension,
                deviation_value=report.deviation_value,
                direction=report.deviation_direction,
                unit=expert_point.unit,
            ),
            improvement_target=_format_improvement_target(expert_point),
            improvement_method=_get_improvement_method(
                report.dimension, report.deviation_direction
            ),
            impact_score=report.impact_score or 0.0,
            reliability_level=reliability_level,
            reliability_note=reliability_note,
        )
        session.add(advice)
        advice_list.append(advice)

    await session.flush()

    # Sort by impact_score DESC before returning
    advice_list.sort(key=lambda a: a.impact_score, reverse=True)

    logger.info(
        "Generated %d coaching advice records for task %s",
        len(advice_list), task_id,
    )
    return advice_list
