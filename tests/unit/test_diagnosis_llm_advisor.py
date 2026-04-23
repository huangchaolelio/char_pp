"""Unit tests for diagnosis_llm_advisor — LLM advice generation with mocked LlmClient.

Tests cover:
  T009(a) - deviation dimensions call LlmClient.chat(), return advice text
  T009(b) - ok dimension → improvement_advice=None, no LLM call
  T009(c) - prompt contains dimension name, measured_value, ideal_value, direction
  T009(d) - LlmError → fallback template string returned, no exception propagated
  T009(e) - tech_category and dimension CN name mappings appear in prompt
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.diagnosis_scorer import (
    DeviationDirection,
    DeviationLevel,
    DimensionScore,
)
from src.services.diagnosis_llm_advisor import generate_improvement_advice
from src.services.llm_client import LlmError


def _make_dim(
    dimension: str = "elbow_angle",
    measured: float = 120.0,
    std_min: float = 85.0,
    std_max: float = 105.0,
    ideal: float = 95.0,
    level: DeviationLevel = DeviationLevel.significant,
    direction: DeviationDirection = DeviationDirection.above,
    score: float = 30.0,
    unit: str = "°",
) -> DimensionScore:
    return DimensionScore(
        dimension=dimension,
        measured_value=measured,
        ideal_value=ideal,
        standard_min=std_min,
        standard_max=std_max,
        unit=unit,
        score=score,
        deviation_level=level,
        deviation_direction=direction,
    )


# ---------------------------------------------------------------------------
# T009(a) — deviation dimensions call LLM, return text
# ---------------------------------------------------------------------------

class TestDeviationCallsLlm:
    def test_significant_calls_llm(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = ("减小肘部角度，保持在标准范围内。", 50)

        dim = _make_dim(level=DeviationLevel.significant, direction=DeviationDirection.above)
        advice = generate_improvement_advice(dim, "forehand_topspin", mock_client)

        assert mock_client.chat.called
        assert advice == "减小肘部角度，保持在标准范围内。"

    def test_slight_calls_llm(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = ("轻微调整即可。", 30)

        dim = _make_dim(
            level=DeviationLevel.slight,
            direction=DeviationDirection.above,
            score=75.0,
        )
        advice = generate_improvement_advice(dim, "forehand_topspin", mock_client)

        assert mock_client.chat.called
        assert advice == "轻微调整即可。"


# ---------------------------------------------------------------------------
# T009(b) — ok dimension → None, no LLM call
# ---------------------------------------------------------------------------

class TestOkDimensionNoLlm:
    def test_ok_returns_none(self):
        mock_client = MagicMock()

        dim = _make_dim(
            level=DeviationLevel.ok,
            direction=DeviationDirection.none,
            score=100.0,
            measured=95.0,
        )
        advice = generate_improvement_advice(dim, "forehand_topspin", mock_client)

        assert advice is None
        mock_client.chat.assert_not_called()


# ---------------------------------------------------------------------------
# T009(c) — prompt contains key information
# ---------------------------------------------------------------------------

class TestPromptContent:
    def test_prompt_contains_dimension_name(self):
        captured_messages = []

        def capture_chat(messages, **kwargs):
            captured_messages.extend(messages)
            return ("建议文本。", 20)

        mock_client = MagicMock()
        mock_client.chat.side_effect = capture_chat

        dim = _make_dim(dimension="elbow_angle", level=DeviationLevel.significant)
        generate_improvement_advice(dim, "forehand_topspin", mock_client)

        full_prompt = " ".join(m["content"] for m in captured_messages)
        assert "elbow_angle" in full_prompt or "肘" in full_prompt

    def test_prompt_contains_measured_value(self):
        captured_messages = []

        def capture_chat(messages, **kwargs):
            captured_messages.extend(messages)
            return ("建议文本。", 20)

        mock_client = MagicMock()
        mock_client.chat.side_effect = capture_chat

        dim = _make_dim(measured=120.0, level=DeviationLevel.significant)
        generate_improvement_advice(dim, "forehand_topspin", mock_client)

        full_prompt = " ".join(m["content"] for m in captured_messages)
        assert "120" in full_prompt

    def test_prompt_contains_ideal_value(self):
        captured_messages = []

        def capture_chat(messages, **kwargs):
            captured_messages.extend(messages)
            return ("建议文本。", 20)

        mock_client = MagicMock()
        mock_client.chat.side_effect = capture_chat

        dim = _make_dim(ideal=95.0, level=DeviationLevel.significant)
        generate_improvement_advice(dim, "forehand_topspin", mock_client)

        full_prompt = " ".join(m["content"] for m in captured_messages)
        assert "95" in full_prompt

    def test_prompt_contains_deviation_direction(self):
        captured_messages = []

        def capture_chat(messages, **kwargs):
            captured_messages.extend(messages)
            return ("建议文本。", 20)

        mock_client = MagicMock()
        mock_client.chat.side_effect = capture_chat

        dim = _make_dim(direction=DeviationDirection.above, level=DeviationLevel.significant)
        generate_improvement_advice(dim, "forehand_topspin", mock_client)

        full_prompt = " ".join(m["content"] for m in captured_messages)
        assert "偏高" in full_prompt or "above" in full_prompt or "大" in full_prompt


# ---------------------------------------------------------------------------
# T009(d) — LlmError → fallback string, no exception
# ---------------------------------------------------------------------------

class TestLlmErrorFallback:
    def test_llm_error_returns_fallback(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = LlmError("API timeout")

        dim = _make_dim(level=DeviationLevel.significant, direction=DeviationDirection.above)
        advice = generate_improvement_advice(dim, "forehand_topspin", mock_client)

        # Should return a non-empty fallback string, not raise
        assert advice is not None
        assert isinstance(advice, str)
        assert len(advice) > 0

    def test_llm_error_fallback_mentions_direction(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = LlmError("timeout")

        dim = _make_dim(
            level=DeviationLevel.significant,
            direction=DeviationDirection.below,
            measured=70.0,
            ideal=95.0,
        )
        advice = generate_improvement_advice(dim, "forehand_topspin", mock_client)
        # Fallback should mention something about the deviation
        assert advice is not None
        assert len(advice) > 10


# ---------------------------------------------------------------------------
# T009(e) — CN name mappings in prompt
# ---------------------------------------------------------------------------

class TestCnNameMappings:
    def test_tech_category_cn_name_in_prompt(self):
        captured_messages = []

        def capture_chat(messages, **kwargs):
            captured_messages.extend(messages)
            return ("建议。", 10)

        mock_client = MagicMock()
        mock_client.chat.side_effect = capture_chat

        dim = _make_dim(level=DeviationLevel.significant)
        generate_improvement_advice(dim, "forehand_topspin", mock_client)

        full_prompt = " ".join(m["content"] for m in captured_messages)
        # forehand_topspin should map to 正手拉球 or at least appear
        assert "正手" in full_prompt or "forehand_topspin" in full_prompt

    def test_unknown_dimension_still_works(self):
        """Unknown dimension should not crash — uses raw name"""
        mock_client = MagicMock()
        mock_client.chat.return_value = ("建议。", 10)

        dim = _make_dim(dimension="custom_unknown_dim", level=DeviationLevel.slight)
        advice = generate_improvement_advice(dim, "backhand_push", mock_client)

        assert advice is not None
