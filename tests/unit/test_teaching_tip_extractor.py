"""Unit tests for TeachingTipExtractor — T006 (TDD).

Tests:
  1. Transcript with technical coaching content → returns ≥1 TeachingTip items
  2. Transcript with no technical coaching content (pure demo) → returns empty list
  3. LLM call timeout (30s) → graceful degradation, returns empty list, no exception
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

# These imports will fail until T007 is implemented — that's expected (TDD)
from src.services.teaching_tip_extractor import TeachingTipData, TeachingTipExtractor


SAMPLE_SENTENCES_TECHNICAL = [
    {"start": 0.0, "end": 2.5, "text": "今天我们学习正手攻球", "confidence": 0.92},
    {"start": 2.5, "end": 5.0, "text": "引拍阶段保持放松，不要提前发力", "confidence": 0.91},
    {"start": 5.0, "end": 8.0, "text": "击球瞬间手腕要有爆发性摩擦", "confidence": 0.93},
    {"start": 8.0, "end": 11.0, "text": "随挥要充分，不要收拍过早", "confidence": 0.90},
    {"start": 11.0, "end": 14.0, "text": "重心要主动前迎，不要等球", "confidence": 0.88},
]

SAMPLE_SENTENCES_NO_TECH = [
    {"start": 0.0, "end": 2.0, "text": "好，我们来示范一下", "confidence": 0.85},
    {"start": 2.0, "end": 4.0, "text": "（击球声）", "confidence": 0.70},
    {"start": 4.0, "end": 6.0, "text": "（击球声）", "confidence": 0.71},
]


def _make_llm_is_technical_response(is_technical: bool) -> MagicMock:
    """Build a mock ChatCompletion response for the is_technical check."""
    import json
    msg = MagicMock()
    msg.content = json.dumps({"is_technical": is_technical, "reason": "test"})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=100, completion_tokens=10)
    response.model = "gpt-4o-mini"
    return response


def _make_llm_tips_response(tips: list[dict]) -> MagicMock:
    """Build a mock ChatCompletion response for the tip extraction step."""
    import json
    msg = MagicMock()
    msg.content = json.dumps({"tips": tips})
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=200, completion_tokens=50)
    response.model = "gpt-4o-mini"
    return response


class TestTeachingTipExtractor:

    def setup_method(self):
        self.extractor = TeachingTipExtractor(openai_api_key="test-key", model="gpt-4o-mini")
        self.task_id = uuid.uuid4()

    # ── Scenario 1: Technical coaching content ────────────────────────────────

    def test_technical_transcript_returns_tips(self):
        """Technical coaching transcript produces ≥1 TeachingTip entries."""
        mock_tips_payload = [
            {"tech_phase": "preparation", "tip_text": "引拍阶段保持放松，不要提前发力", "confidence": 0.91},
            {"tech_phase": "contact", "tip_text": "击球瞬间手腕要有爆发性摩擦", "confidence": 0.93},
            {"tech_phase": "follow_through", "tip_text": "随挥要充分，不要收拍过早", "confidence": 0.90},
        ]
        is_tech_resp = _make_llm_is_technical_response(True)
        tips_resp = _make_llm_tips_response(mock_tips_payload)

        with patch.object(self.extractor._client.chat.completions, "create",
                          side_effect=[is_tech_resp, tips_resp]):
            result = self.extractor.extract(
                SAMPLE_SENTENCES_TECHNICAL,
                action_type="forehand_topspin",
                task_id=self.task_id,
            )

        assert len(result) >= 1
        for tip in result:
            assert isinstance(tip, TeachingTipData)
            assert tip.tip_text
            assert tip.tech_phase in ("preparation", "contact", "follow_through", "footwork", "general")
            assert 0.0 <= tip.confidence <= 1.0
            assert tip.action_type == "forehand_topspin"
            assert tip.task_id == self.task_id

    def test_tips_grouped_by_tech_phase(self):
        """Tips from different phases are returned as separate entries (not merged)."""
        mock_tips_payload = [
            {"tech_phase": "preparation", "tip_text": "引拍放松", "confidence": 0.88},
            {"tech_phase": "contact", "tip_text": "击球摩擦", "confidence": 0.92},
        ]
        is_tech_resp = _make_llm_is_technical_response(True)
        tips_resp = _make_llm_tips_response(mock_tips_payload)

        with patch.object(self.extractor._client.chat.completions, "create",
                          side_effect=[is_tech_resp, tips_resp]):
            result = self.extractor.extract(
                SAMPLE_SENTENCES_TECHNICAL,
                action_type="forehand_topspin",
                task_id=self.task_id,
            )

        phases = [t.tech_phase for t in result]
        assert "preparation" in phases
        assert "contact" in phases

    # ── Scenario 2: No technical coaching content ─────────────────────────────

    def test_non_technical_transcript_returns_empty(self):
        """Pure demo transcript (no coaching language) → empty list."""
        is_tech_resp = _make_llm_is_technical_response(False)

        with patch.object(self.extractor._client.chat.completions, "create",
                          return_value=is_tech_resp):
            result = self.extractor.extract(
                SAMPLE_SENTENCES_NO_TECH,
                action_type="forehand_topspin",
                task_id=self.task_id,
            )

        assert result == []

    def test_empty_sentences_returns_empty(self):
        """Empty transcript → empty list without calling LLM."""
        with patch.object(self.extractor._client.chat.completions, "create") as mock_create:
            result = self.extractor.extract(
                [],
                action_type="forehand_topspin",
                task_id=self.task_id,
            )

        assert result == []
        mock_create.assert_not_called()

    # ── Scenario 3: LLM timeout / exception → graceful degradation ───────────

    def test_llm_timeout_returns_empty_no_exception(self):
        """LLM call timeout (openai.Timeout) → returns [] without raising."""
        import openai

        with patch.object(self.extractor._client.chat.completions, "create",
                          side_effect=openai.APITimeoutError(request=MagicMock())):
            result = self.extractor.extract(
                SAMPLE_SENTENCES_TECHNICAL,
                action_type="forehand_topspin",
                task_id=self.task_id,
            )

        assert result == []

    def test_llm_api_error_returns_empty_no_exception(self):
        """Any OpenAI API error → graceful degradation, returns []."""
        import openai

        with patch.object(self.extractor._client.chat.completions, "create",
                          side_effect=openai.APIError("test error", request=MagicMock(), body=None)):
            result = self.extractor.extract(
                SAMPLE_SENTENCES_TECHNICAL,
                action_type="forehand_topspin",
                task_id=self.task_id,
            )

        assert result == []

    def test_llm_invalid_json_returns_empty_no_exception(self):
        """LLM returns malformed JSON → graceful degradation, returns []."""
        msg = MagicMock()
        msg.content = "not valid json {{ broken"
        choice = MagicMock()
        choice.message = msg
        response = MagicMock()
        response.choices = [choice]
        response.usage = MagicMock(prompt_tokens=100, completion_tokens=5)
        response.model = "gpt-4o-mini"

        with patch.object(self.extractor._client.chat.completions, "create",
                          return_value=response):
            result = self.extractor.extract(
                SAMPLE_SENTENCES_TECHNICAL,
                action_type="forehand_topspin",
                task_id=self.task_id,
            )

        assert result == []
