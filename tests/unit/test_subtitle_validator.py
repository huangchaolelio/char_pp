"""Unit tests for SubtitleValidator — T038.

Tests verify:
- SRT parsing: correct timestamps and text extraction
- Sync validation: subtitle vs Whisper transcript timestamp comparison
- Embedded SRT extraction via ffmpeg (mocked)

Run with: pytest tests/unit/test_subtitle_validator.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.subtitle_validator import SubtitleValidationResult, SubtitleValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator():
    return SubtitleValidator()


def _make_srt(entries: list[tuple[str, str, str]]) -> str:
    """Build SRT content from (index, timecode_line, text) tuples."""
    blocks = []
    for i, (tc, text) in enumerate(entries, start=1):
        blocks.append(f"{i}\n{tc}\n{text}\n")
    return "\n".join(blocks)


def _make_transcript_sentences(entries: list[tuple[float, float, str]]) -> list[dict]:
    """Build Whisper-style sentence list from (start, end, text) tuples."""
    return [
        {"start": s, "end": e, "text": t, "confidence": 0.9}
        for s, e, t in entries
    ]


# ---------------------------------------------------------------------------
# TestParseSrt
# ---------------------------------------------------------------------------


class TestParseSrt:
    def test_parses_three_entries(self, tmp_path):
        srt_content = _make_srt([
            ("00:00:01,000 --> 00:00:03,000", "肘部角度应保持在九十度"),
            ("00:00:05,500 --> 00:00:07,200", "注意重心要前移"),
            ("00:01:10,100 --> 00:01:12,000", "击球瞬间手腕要放松"),
        ])
        srt_file = tmp_path / "sub.srt"
        srt_file.write_text(srt_content, encoding="utf-8")

        result = SubtitleValidator.parse_srt(srt_file)

        assert len(result) == 3
        assert result[0]["start_s"] == pytest.approx(1.0)
        assert result[0]["end_s"] == pytest.approx(3.0)
        assert result[0]["text"] == "肘部角度应保持在九十度"
        assert result[1]["start_s"] == pytest.approx(5.5)
        assert result[2]["start_s"] == pytest.approx(70.1)

    def test_empty_file_returns_empty_list(self, tmp_path):
        srt_file = tmp_path / "empty.srt"
        srt_file.write_text("", encoding="utf-8")

        result = SubtitleValidator.parse_srt(srt_file)

        assert result == []

    def test_no_timecode_returns_empty_list(self, tmp_path):
        """File exists but has no SRT timecode format → unsupported."""
        srt_file = tmp_path / "bad.srt"
        srt_file.write_text("This is not a valid SRT file.\nJust plain text.\n", encoding="utf-8")

        result = SubtitleValidator.parse_srt(srt_file)

        assert result == []

    def test_zero_boundary_timecode(self, tmp_path):
        srt_content = _make_srt([
            ("00:00:00,000 --> 00:00:00,500", "开始"),
        ])
        srt_file = tmp_path / "zero.srt"
        srt_file.write_text(srt_content, encoding="utf-8")

        result = SubtitleValidator.parse_srt(srt_file)

        assert len(result) == 1
        assert result[0]["start_s"] == pytest.approx(0.0)
        assert result[0]["end_s"] == pytest.approx(0.5)

    def test_multiline_text_joined(self, tmp_path):
        """Multi-line subtitle text should be joined."""
        srt_file = tmp_path / "multi.srt"
        srt_file.write_text(
            "1\n00:00:01,000 --> 00:00:03,000\n第一行\n第二行\n\n",
            encoding="utf-8",
        )

        result = SubtitleValidator.parse_srt(srt_file)

        assert len(result) == 1
        assert "第一行" in result[0]["text"]


# ---------------------------------------------------------------------------
# TestValidateSync
# ---------------------------------------------------------------------------


class TestValidateSync:
    def test_matching_content_within_threshold_is_valid(self, validator):
        """Same text in subtitle and transcript with 0.5s offset → valid."""
        srt_sentences = [{"start_s": 1.0, "end_s": 3.0, "text": "肘部角度应保持在九十度"}]
        transcript = _make_transcript_sentences([(1.5, 3.5, "肘部角度应保持在九十度")])

        result = validator.validate(transcript, srt_sentences)

        assert result.is_valid is True
        assert result.fallback_suffix is None
        assert result.max_offset_s == pytest.approx(0.5)

    def test_matching_content_exceeding_threshold_is_invalid(self, validator):
        """Same text but 3.2s offset → not synced."""
        srt_sentences = [{"start_s": 1.0, "end_s": 3.0, "text": "重心要前移击球"}]
        transcript = _make_transcript_sentences([(4.2, 6.0, "重心要前移击球")])

        result = validator.validate(transcript, srt_sentences)

        assert result.is_valid is False
        assert result.fallback_suffix is not None
        assert "subtitle_out_of_sync" in result.fallback_suffix
        assert result.max_offset_s == pytest.approx(3.2)

    def test_no_text_overlap_is_valid(self, validator):
        """Completely different text → no match pairs → conservative: treat as valid."""
        srt_sentences = [{"start_s": 1.0, "end_s": 2.0, "text": "这是字幕文字"}]
        transcript = _make_transcript_sentences([(10.0, 12.0, "完全不同的转录内容")])

        result = validator.validate(transcript, srt_sentences)

        assert result.is_valid is True
        assert result.fallback_suffix is None

    def test_empty_srt_is_valid(self, validator):
        """No subtitle sentences → nothing to validate → valid (no subtitles present)."""
        transcript = _make_transcript_sentences([(1.0, 2.0, "任意转录")])

        result = validator.validate(transcript, [])

        assert result.is_valid is True
        assert result.fallback_suffix is None

    def test_empty_transcript_is_valid(self, validator):
        """No transcript sentences → cannot compare → conservative: valid."""
        srt_sentences = [{"start_s": 1.0, "end_s": 2.0, "text": "字幕文本"}]

        result = validator.validate([], srt_sentences)

        assert result.is_valid is True
        assert result.fallback_suffix is None

    def test_single_sentence_match(self, validator):
        """Single matching pair, exactly 2.0s offset → boundary: still valid (not strictly >)."""
        srt_sentences = [{"start_s": 0.0, "end_s": 2.0, "text": "标准示范动作请注意"}]
        transcript = _make_transcript_sentences([(2.0, 4.0, "标准示范动作请注意")])

        result = validator.validate(transcript, srt_sentences)

        # 2.0s == threshold, not > threshold → valid
        assert result.is_valid is True

    def test_fallback_suffix_includes_offset_value(self, validator):
        """Fallback suffix should contain the numeric max_offset value."""
        srt_sentences = [{"start_s": 0.0, "end_s": 2.0, "text": "肘部角度保持正确"}]
        transcript = _make_transcript_sentences([(5.5, 7.0, "肘部角度保持正确")])

        result = validator.validate(transcript, srt_sentences)

        assert result.is_valid is False
        # suffix format: "subtitle_out_of_sync: 5.5s"
        assert "5.5" in result.fallback_suffix


# ---------------------------------------------------------------------------
# TestExtractEmbeddedSrt
# ---------------------------------------------------------------------------


class TestExtractEmbeddedSrt:
    def test_ffmpeg_success_returns_true(self, tmp_path):
        """ffmpeg exits 0 and creates non-empty file → True."""
        output_srt = tmp_path / "out.srt"

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            # Simulate ffmpeg creating the output file
            output_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
            result = SubtitleValidator.extract_embedded_srt(
                video_path=tmp_path / "video.mp4",
                output_srt=output_srt,
            )

        assert result is True

    def test_ffmpeg_no_subtitle_stream_returns_false(self, tmp_path):
        """ffmpeg exits non-zero (no subtitle stream) → False."""
        output_srt = tmp_path / "out.srt"

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Stream specifier 's:0' in filtergraph does not match any streams"

        with patch("subprocess.run", return_value=mock_result):
            result = SubtitleValidator.extract_embedded_srt(
                video_path=tmp_path / "video.mp4",
                output_srt=output_srt,
            )

        assert result is False

    def test_ffmpeg_empty_output_returns_false(self, tmp_path):
        """ffmpeg exits 0 but produces empty file → False."""
        output_srt = tmp_path / "out.srt"

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            output_srt.write_text("", encoding="utf-8")  # empty file
            result = SubtitleValidator.extract_embedded_srt(
                video_path=tmp_path / "video.mp4",
                output_srt=output_srt,
            )

        assert result is False

    def test_ffmpeg_not_found_returns_false(self, tmp_path):
        """ffmpeg not in PATH → False (non-fatal)."""
        output_srt = tmp_path / "out.srt"

        with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
            result = SubtitleValidator.extract_embedded_srt(
                video_path=tmp_path / "video.mp4",
                output_srt=output_srt,
            )

        assert result is False


# ---------------------------------------------------------------------------
# TestUnsupportedFormat (via validate_from_video integration)
# ---------------------------------------------------------------------------


class TestUnsupportedFormat:
    def test_empty_srt_parse_yields_unsupported_suffix(self, validator, tmp_path):
        """If parse_srt returns [] (bad format), validate_from_video returns unsupported suffix."""
        bad_srt = tmp_path / "bad.srt"
        bad_srt.write_text("not a real srt", encoding="utf-8")

        # parse_srt([]) with non-empty transcript → unsupported format
        srt_sentences = SubtitleValidator.parse_srt(bad_srt)
        assert srt_sentences == []  # pre-condition

        # When srt_sentences is empty AND we know ffmpeg succeeded (i.e. file existed),
        # the caller should record "subtitle_unsupported_format"
        # We test the helper that produces the suffix:
        suffix = SubtitleValidator.unsupported_format_suffix()
        assert "subtitle_unsupported_format" in suffix
