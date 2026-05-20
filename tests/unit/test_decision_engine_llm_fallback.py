"""Feature-021 T026 — decision_engine LLM 兜底单测.

覆盖（research.md § R3）：
- 模糊区间分段 + LLM 返回有效 JSON → 按 LLM 决策
- LLM 返回非 JSON → uncertain + rejection_reason='llm_response_invalid'
- LLM 抛超时异常 → uncertain + rejection_reason='curation_llm_unavailable'
- LLM 返回非枚举 decision → uncertain + 'llm_response_invalid'
- LLM accepted 时 rejection_reason 强制为 None
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.services.curation.decision_engine import decide
from src.services.curation.rubric_loader import load


@pytest.fixture(scope="module")
def rubric():
    from src.services.curation import rubric_loader as rl
    rl.reset_cache()
    return load("v1")


def _make_llm_with_response(text: str) -> MagicMock:
    client = MagicMock()
    client.chat.return_value = (text, 100)
    return client


def _ambiguous_text() -> str:
    """该文本规则路得分约 0.53，落入 (0.3, 0.7) 模糊区间，触发 LLM 兜底."""
    return "大家好我们这个动作做得比较好"


# ── 正常 LLM 路径 ─────────────────────────────────────────────────────


def test_llm_returns_accepted(rubric) -> None:
    payload = {
        "decision": "accepted",
        "validity_score": 0.85,
        "rejection_reason": None,
        "rationale": "完整动作演示",
    }
    client = _make_llm_with_response(json.dumps(payload))
    res = decide(
        segment_text=_ambiguous_text(),
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=client,
    )
    assert res.decision == "accepted"
    assert res.validity_score == 0.85
    assert res.rejection_reason is None
    assert res.decision_source == "llm"


def test_llm_returns_rejected_with_reason(rubric) -> None:
    payload = {
        "decision": "rejected",
        "validity_score": 0.2,
        "rejection_reason": "off_topic",
        "rationale": "未涉及目标技术",
    }
    client = _make_llm_with_response(json.dumps(payload))
    res = decide(
        segment_text=_ambiguous_text(),
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=client,
    )
    assert res.decision == "rejected"
    assert res.rejection_reason == "off_topic"
    assert res.decision_source == "llm"


# ── 失败路径：LLM 不可用 → uncertain ─────────────────────────────────


def test_llm_raises_exception_falls_back_to_uncertain(rubric) -> None:
    client = MagicMock()
    client.chat.side_effect = TimeoutError("LLM timeout")
    res = decide(
        segment_text=_ambiguous_text(),
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=client,
    )
    assert res.decision == "uncertain"
    assert res.rejection_reason == "curation_llm_unavailable"
    assert res.decision_source == "llm"


def test_llm_returns_non_json_falls_back_to_uncertain(rubric) -> None:
    client = _make_llm_with_response("not a json string")
    res = decide(
        segment_text=_ambiguous_text(),
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=client,
    )
    assert res.decision == "uncertain"
    assert res.rejection_reason == "curation_llm_unavailable"


def test_llm_returns_invalid_decision_value_yields_uncertain(rubric) -> None:
    payload = {
        "decision": "MAYBE",  # 非枚举值
        "validity_score": 0.5,
    }
    client = _make_llm_with_response(json.dumps(payload))
    res = decide(
        segment_text=_ambiguous_text(),
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=client,
    )
    assert res.decision == "uncertain"
    assert res.rejection_reason == "llm_response_invalid"


def test_rule_clear_path_does_not_invoke_llm(rubric) -> None:
    """规则路得分明确（≥ 0.7 或 ≤ 0.3）时 LLM 必须 *不被调用*。"""
    client = MagicMock()
    # 高分文本（accepted 路径）
    decide(
        segment_text="技术要点示范一下，注意看动作要领，重心和转腰是关键技术，标准动作",
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=client,
    )
    assert client.chat.call_count == 0

    # 低分文本（rejected 路径）
    decide(
        segment_text="本场比赛的关键时刻，本场胜利属于他，颁奖仪式即将开始",
        rubric=rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=client,
    )
    assert client.chat.call_count == 0
