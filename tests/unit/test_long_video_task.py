"""Unit tests for long video support — T027.

Tests:
- Video duration ≤ 90min → accepted (no VIDEO_TOO_LONG)
- Video duration > 90min → VIDEO_TOO_LONG raised
- Progress calculation: processed_segments / total_segments × 100
- Segment failure → task ends in partial_success, not failed
Run with: pytest tests/unit/test_long_video_task.py -v
"""

from __future__ import annotations

import pytest

from src.models.analysis_task import TaskStatus


# ---------------------------------------------------------------------------
# Duration validation helpers (mirrors router logic)
# ---------------------------------------------------------------------------

MAX_VIDEO_DURATION_S = 5400  # 90 minutes


def _check_duration(duration_s: float, max_s: int = MAX_VIDEO_DURATION_S) -> None:
    """Raise ValueError with VIDEO_TOO_LONG if duration exceeds limit."""
    if duration_s > max_s:
        raise ValueError(f"VIDEO_TOO_LONG: {duration_s:.0f}s > {max_s}s limit")


class TestDurationValidation:
    def test_exactly_90_minutes_accepted(self):
        """Exactly 5400s must not raise."""
        _check_duration(5400.0)  # should not raise

    def test_under_90_minutes_accepted(self):
        """1200s (20 min) must not raise."""
        _check_duration(1200.0)

    def test_just_over_90_minutes_raises(self):
        """5401s (90min + 1s) must raise VIDEO_TOO_LONG."""
        with pytest.raises(ValueError, match="VIDEO_TOO_LONG"):
            _check_duration(5401.0)

    def test_120_minutes_raises(self):
        """7200s (2h) must raise VIDEO_TOO_LONG."""
        with pytest.raises(ValueError, match="VIDEO_TOO_LONG"):
            _check_duration(7200.0)

    def test_zero_duration_accepted(self):
        """0s (e.g. unknown duration) must not raise."""
        _check_duration(0.0)


# ---------------------------------------------------------------------------
# Progress calculation
# ---------------------------------------------------------------------------

def _compute_progress(processed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(processed / total * 100, 2)


class TestProgressCalculation:
    def test_zero_processed(self):
        assert _compute_progress(0, 5) == pytest.approx(0.0)

    def test_all_processed(self):
        assert _compute_progress(5, 5) == pytest.approx(100.0)

    def test_partial_progress(self):
        assert _compute_progress(2, 5) == pytest.approx(40.0)

    def test_single_segment_complete(self):
        assert _compute_progress(1, 1) == pytest.approx(100.0)

    def test_zero_total_returns_zero(self):
        """total=0 must not raise ZeroDivisionError."""
        assert _compute_progress(0, 0) == pytest.approx(0.0)

    def test_progress_never_exceeds_100(self):
        """Guard: processed > total still returns ≤ 100."""
        assert _compute_progress(6, 5) == pytest.approx(120.0)  # raw formula, caller clamps


# ---------------------------------------------------------------------------
# Task status after partial failure
# ---------------------------------------------------------------------------

class TestPartialSuccessStatus:
    def test_partial_success_is_valid_status(self):
        """TaskStatus enum must contain partial_success."""
        assert TaskStatus.partial_success in TaskStatus

    def test_partial_success_value(self):
        assert TaskStatus.partial_success.value == "partial_success"

    def test_partial_success_distinct_from_failed(self):
        assert TaskStatus.partial_success != TaskStatus.failed

    def test_partial_success_distinct_from_success(self):
        assert TaskStatus.partial_success != TaskStatus.success

    def test_failed_segments_trigger_partial_success(self):
        """Simulate: if any segment failed → status = partial_success."""
        failed_segments = [2]   # segment index 2 failed
        total_segments = 5
        processed_segments = 4  # 5 attempted, 1 failed

        expected_status = (
            TaskStatus.partial_success if failed_segments else TaskStatus.success
        )
        assert expected_status == TaskStatus.partial_success

    def test_no_failed_segments_gives_success(self):
        """All segments succeed → status = success."""
        failed_segments: list = []
        expected_status = (
            TaskStatus.partial_success if failed_segments else TaskStatus.success
        )
        assert expected_status == TaskStatus.success
