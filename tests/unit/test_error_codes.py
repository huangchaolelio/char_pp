"""Unit tests — Feature 015 error code constants and formatter (T006).

Covers FR-016: structured error prefixes let operations scripts grep failures
and map them to runbooks without parsing free-form error text.
"""

from __future__ import annotations

import pytest

from src.services.kb_extraction_pipeline.error_codes import (
    ACTION_CLASSIFY_FAILED,
    ALL_ERROR_CODES,
    LLM_CALL_FAILED,
    LLM_JSON_PARSE,
    LLM_UNCONFIGURED,
    POSE_MODEL_LOAD_FAILED,
    POSE_NO_KEYPOINTS,
    VIDEO_QUALITY_REJECTED,
    WHISPER_LOAD_FAILED,
    WHISPER_NO_AUDIO,
    format_error,
)


pytestmark = pytest.mark.unit


class TestErrorCodeConstants:
    def test_all_codes_are_non_empty_strings(self) -> None:
        for code in ALL_ERROR_CODES:
            assert isinstance(code, str)
            assert code
            # Prefixes are SCREAMING_SNAKE_CASE by convention.
            assert code == code.upper()
            assert " " not in code

    def test_expected_codes_documented(self) -> None:
        """Data-model.md lists 9 prefixes; drift in either direction breaks
        the FR-016 contract."""
        expected = {
            "VIDEO_QUALITY_REJECTED",
            "POSE_NO_KEYPOINTS",
            "POSE_MODEL_LOAD_FAILED",
            "WHISPER_LOAD_FAILED",
            "WHISPER_NO_AUDIO",
            "ACTION_CLASSIFY_FAILED",
            "LLM_UNCONFIGURED",
            "LLM_JSON_PARSE",
            "LLM_CALL_FAILED",
        }
        assert ALL_ERROR_CODES == expected

    def test_individual_constants_exported(self) -> None:
        assert VIDEO_QUALITY_REJECTED == "VIDEO_QUALITY_REJECTED"
        assert POSE_NO_KEYPOINTS == "POSE_NO_KEYPOINTS"
        assert POSE_MODEL_LOAD_FAILED == "POSE_MODEL_LOAD_FAILED"
        assert WHISPER_LOAD_FAILED == "WHISPER_LOAD_FAILED"
        assert WHISPER_NO_AUDIO == "WHISPER_NO_AUDIO"
        assert ACTION_CLASSIFY_FAILED == "ACTION_CLASSIFY_FAILED"
        assert LLM_UNCONFIGURED == "LLM_UNCONFIGURED"
        assert LLM_JSON_PARSE == "LLM_JSON_PARSE"
        assert LLM_CALL_FAILED == "LLM_CALL_FAILED"


class TestFormatError:
    def test_basic_format(self) -> None:
        assert (
            format_error("VIDEO_QUALITY_REJECTED", "fps=12 vs 15")
            == "VIDEO_QUALITY_REJECTED: fps=12 vs 15"
        )

    def test_uses_colon_space_separator(self) -> None:
        """The separator ': ' is the contract — ops scripts split on it."""
        msg = format_error(LLM_JSON_PARSE, "missing 'dimension' key")
        code, _, details = msg.partition(": ")
        assert code == "LLM_JSON_PARSE"
        assert details == "missing 'dimension' key"

    def test_empty_details_still_valid(self) -> None:
        assert format_error(LLM_UNCONFIGURED, "") == "LLM_UNCONFIGURED: "

    def test_accepts_custom_prefix(self) -> None:
        """format_error does not hard-code the known constants — callers may
        introduce new prefixes in future without changing this helper."""
        assert format_error("CUSTOM_CODE", "detail") == "CUSTOM_CODE: detail"
