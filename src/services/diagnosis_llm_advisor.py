"""Diagnosis LLM advisor — generates natural-language improvement advice.

For deviation dimensions (slight/significant), constructs a Chinese prompt and
calls LlmClient.chat(). For ok dimensions, returns None without calling LLM.

On LlmError, returns a fallback template string (never raises to caller).
"""

from __future__ import annotations

import logging
from typing import Optional

from src.services.diagnosis_scorer import DeviationDirection, DeviationLevel, DimensionScore
from src.services.llm_client import LlmClient, LlmError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chinese name mappings
# ---------------------------------------------------------------------------

DIMENSION_CN_NAMES: dict[str, str] = {
    "elbow_angle": "肘部角度",
    "swing_trajectory": "挥拍轨迹",
    "contact_timing": "击球时机",
    "weight_transfer": "重心转移",
    "hip_rotation": "髋部转动",
    "wrist_angle": "手腕角度",
    "knee_bend": "膝关节弯曲",
    "shoulder_rotation": "肩部转动",
    "foot_position": "步法位置",
    "body_balance": "身体平衡",
}

ACTION_CN_NAMES: dict[str, str] = {
    "forehand_topspin": "正手拉球",
    "forehand_attack": "正手攻球",
    "forehand_chop_long": "正手劈长",
    "forehand_counter": "正手快带",
    "forehand_loop_underspin": "正手起下旋",
    "forehand_flick": "正手挑打",
    "forehand_position": "正手跑位",
    "forehand_general": "正手通用",
    "backhand_push": "反手推挡",
    "backhand_topspin": "反手拉球",
    "backhand_flick": "反手弹打",
    "backhand_general": "反手通用",
}

_DIRECTION_CN: dict[DeviationDirection, str] = {
    DeviationDirection.above: "偏高",
    DeviationDirection.below: "偏低",
    DeviationDirection.none: "正常",
}


# ---------------------------------------------------------------------------
# Fallback templates
# ---------------------------------------------------------------------------

def _fallback_advice(dim: DimensionScore) -> str:
    direction_cn = _DIRECTION_CN.get(dim.deviation_direction, "偏差")
    dim_cn = DIMENSION_CN_NAMES.get(dim.dimension, dim.dimension)
    diff = abs(dim.measured_value - dim.ideal_value)
    return (
        f"您的{dim_cn}当前值为 {dim.measured_value:.1f}{dim.unit or ''}，"
        f"理想值为 {dim.ideal_value:.1f}{dim.unit or ''}（{direction_cn} {diff:.1f}）。"
        f"建议针对此维度进行专项训练，逐步向理想值靠拢。"
    )


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def generate_improvement_advice(
    dim: DimensionScore,
    tech_category: str,
    llm_client: LlmClient,
) -> Optional[str]:
    """Generate improvement advice for a dimension.

    Returns None for ok dimensions (no LLM call).
    Returns LLM-generated text for slight/significant deviations.
    Falls back to template string on LlmError.
    """
    if dim.deviation_level == DeviationLevel.ok:
        return None

    dim_cn = DIMENSION_CN_NAMES.get(dim.dimension, dim.dimension)
    action_cn = ACTION_CN_NAMES.get(tech_category, tech_category)
    direction_cn = _DIRECTION_CN.get(dim.deviation_direction, "偏差")
    diff = dim.measured_value - dim.ideal_value

    prompt = (
        f"你是一位乒乓球专业教练，请为以下非专业学员动作问题提供简洁、实用的改进建议（2-3句话）：\n\n"
        f"技术动作：{action_cn}\n"
        f"问题维度：{dim_cn}（{dim.dimension}）\n"
        f"学员测量值：{dim.measured_value:.2f} {dim.unit or ''}\n"
        f"理想标准值：{dim.ideal_value:.2f} {dim.unit or ''}\n"
        f"标准范围：[{dim.standard_min:.2f}, {dim.standard_max:.2f}] {dim.unit or ''}\n"
        f"偏差方向：{direction_cn}（差值 {diff:+.2f}）\n\n"
        f"请直接给出针对此偏差的改进建议，不要重复以上数据，用第二人称（您）。"
    )

    messages = [{"role": "user", "content": prompt}]

    try:
        advice_text, _ = llm_client.chat(messages, temperature=0.3)
        return advice_text.strip() or _fallback_advice(dim)
    except LlmError as exc:
        logger.warning(
            "LLM advice generation failed, using fallback",
            extra={
                "dimension": dim.dimension,
                "tech_category": tech_category,
                "error": str(exc),
            },
        )
        return _fallback_advice(dim)
