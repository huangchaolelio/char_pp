"""Feature-021 T025 — decision_engine 规则路单测.

覆盖：
- 高质量教学文本 → accepted（score ≥ threshold_accept）
- 比赛回放 / 采访关键词 → rejected（score ≤ threshold_reject）
- 时长不足 → duration_floor 维度归零
- dim_breakdown 含 5 维全部得分 + 命中明细
- LLM 不可用且落入模糊区间 → uncertain（按 unavailable_decision）
- rejection_reason 选最低维度
"""

from __future__ import annotations

import pytest

from src.services.curation.decision_engine import decide
from src.services.curation.rubric_loader import load


@pytest.fixture(scope="module")
def rubric():
    from src.services.curation import rubric_loader as rl
    rl.reset_cache()
    return load("v1")


# ── 高分路：accepted ───────────────────────────────────────────────


def test_high_quality_teaching_text_yields_accepted(rubric) -> None:
    res = decide(
        segment_text=(
            "下面给大家做一个示范，注意看这个动作要领，"
            "正手拉球的时候重心要转，技术要点是收小臂，关键点是击球瞬间，"
            "标准动作就是这样"
        ),
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=None,
    )
    assert res.decision == "accepted"
    assert res.validity_score >= rubric.threshold_accept
    assert res.decision_source == "rule"
    assert res.rejection_reason is None
    assert res.dim_breakdown.keys() == {
        "tech_keyword", "non_teaching", "coach_dominance",
        "topic_relevance", "duration_floor",
    }


# ── 低分路：rejected ───────────────────────────────────────────────


def test_match_replay_text_yields_rejected(rubric) -> None:
    res = decide(
        segment_text="本场比赛的关键时刻，本场胜利属于他，全场比分定格，颁奖仪式即将开始",
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=None,
    )
    assert res.decision == "rejected"
    assert res.validity_score <= rubric.threshold_reject
    # rejection_reason 来自 5 维中得分最低的维度
    assert res.rejection_reason in {
        "non_teaching_content", "other_speaker", "off_topic", "no_tech_terms",
    }


def test_too_short_segment_zeroes_duration_floor(rubric) -> None:
    """单分段时长 < min_segment_seconds → duration_floor 维度直接 0 分."""
    res = decide(
        segment_text="示范动作要领",
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=2.0,
        llm_client=None,
    )
    assert res.dim_breakdown["duration_floor"]["score"] == 0.0


# ── 模糊区间：LLM 不可用 ────────────────────────────────────────


def test_ambiguous_score_with_no_llm_falls_back_to_uncertain(rubric) -> None:
    """得分落入 (threshold_reject, threshold_accept) 且 llm_client=None
    ⇒ uncertain（按 rubric.unavailable_decision）."""
    res = decide(
        segment_text="大家好我们这个动作做得比较好",
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=None,
    )
    assert res.decision == "uncertain"
    assert rubric.threshold_reject < res.validity_score < rubric.threshold_accept
    assert res.rejection_reason == "curation_llm_unavailable"


# ── dim_breakdown 内容质量 ─────────────────────────────────────


def test_dim_breakdown_contains_matched_keywords(rubric) -> None:
    """tech_keyword 命中后必须把命中关键词记到 dim_breakdown 用于审计."""
    res = decide(
        segment_text="技术要点：示范一下这个动作，重心和转腰是关键技术",
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=None,
    )
    matched = res.dim_breakdown["tech_keyword"]["matched"]
    assert isinstance(matched, list)
    # 至少命中"技术要点"或"重心"等任一关键词
    assert len(matched) >= 1
