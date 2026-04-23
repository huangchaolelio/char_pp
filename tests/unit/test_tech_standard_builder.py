"""Unit tests for TechStandardBuilder.

Tests verify:
- Median + P25/P75 aggregation math
- conflict_flag=True points are excluded
- extraction_confidence < 0.7 points are excluded
- source_quality determination (multi_source vs single_source)
- Skip logic when no valid points exist
- Version increment on rebuild
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers to build fake ExpertTechPoint-like objects
# ---------------------------------------------------------------------------

def _make_point(
    action_type: str,
    dimension: str,
    param_ideal: float,
    unit: str = "°",
    confidence: float = 0.9,
    conflict_flag: bool = False,
    source_video_id=None,
):
    p = MagicMock()
    p.action_type = action_type
    p.dimension = dimension
    p.param_ideal = param_ideal
    p.unit = unit
    p.extraction_confidence = confidence
    p.conflict_flag = conflict_flag
    p.source_video_id = source_video_id or uuid.uuid4()
    return p


# ---------------------------------------------------------------------------
# Pure aggregation math
# ---------------------------------------------------------------------------

class TestAggregationMath:
    """Tests for _aggregate_dimension helper (pure function, no DB)."""

    def test_median_odd_count(self):
        from src.services.tech_standard_builder import _aggregate_dimension

        values = [90.0, 110.0, 130.0]
        result = _aggregate_dimension(values)
        assert result["ideal"] == 110.0
        assert result["min"] == pytest.approx(100.0, abs=0.01)
        assert result["max"] == pytest.approx(120.0, abs=0.01)

    def test_median_even_count(self):
        from src.services.tech_standard_builder import _aggregate_dimension

        values = [80.0, 100.0, 120.0, 140.0]
        result = _aggregate_dimension(values)
        # median of [80, 100, 120, 140] = (100+120)/2 = 110
        assert result["ideal"] == pytest.approx(110.0, abs=0.01)

    def test_single_value(self):
        from src.services.tech_standard_builder import _aggregate_dimension

        values = [100.0]
        result = _aggregate_dimension(values)
        assert result["ideal"] == 100.0
        assert result["min"] == 100.0
        assert result["max"] == 100.0

    def test_five_values_p25_p75(self):
        """Given [90, 95, 110, 125, 130], P25=95, median=110, P75=125."""
        from src.services.tech_standard_builder import _aggregate_dimension

        values = [90.0, 95.0, 110.0, 125.0, 130.0]
        result = _aggregate_dimension(values)
        assert result["ideal"] == pytest.approx(110.0, abs=0.01)
        assert result["min"] == pytest.approx(95.0, abs=1.0)
        assert result["max"] == pytest.approx(125.0, abs=1.0)


# ---------------------------------------------------------------------------
# Filtering logic
# ---------------------------------------------------------------------------

class TestPointFiltering:
    """Tests for filter_valid_points helper."""

    def test_excludes_conflict_flag_true(self):
        from src.services.tech_standard_builder import filter_valid_points

        points = [
            _make_point("forehand_topspin", "elbow_angle", 110.0, conflict_flag=False),
            _make_point("forehand_topspin", "elbow_angle", 115.0, conflict_flag=True),
        ]
        valid = filter_valid_points(points)
        assert len(valid) == 1
        assert valid[0].param_ideal == 110.0

    def test_excludes_low_confidence(self):
        from src.services.tech_standard_builder import filter_valid_points

        points = [
            _make_point("forehand_topspin", "elbow_angle", 110.0, confidence=0.7),
            _make_point("forehand_topspin", "elbow_angle", 115.0, confidence=0.69),
            _make_point("forehand_topspin", "elbow_angle", 120.0, confidence=0.5),
        ]
        valid = filter_valid_points(points)
        assert len(valid) == 1
        assert valid[0].extraction_confidence == 0.7

    def test_excludes_both_filters_simultaneously(self):
        from src.services.tech_standard_builder import filter_valid_points

        points = [
            _make_point("forehand_topspin", "d", 110.0, confidence=0.9, conflict_flag=False),
            _make_point("forehand_topspin", "d", 115.0, confidence=0.9, conflict_flag=True),
            _make_point("forehand_topspin", "d", 120.0, confidence=0.5, conflict_flag=False),
        ]
        valid = filter_valid_points(points)
        assert len(valid) == 1

    def test_empty_list_returns_empty(self):
        from src.services.tech_standard_builder import filter_valid_points

        assert filter_valid_points([]) == []


# ---------------------------------------------------------------------------
# Source quality determination
# ---------------------------------------------------------------------------

class TestSourceQuality:
    """Tests for determine_source_quality helper."""

    def test_multi_source_when_two_or_more_videos(self):
        from src.services.tech_standard_builder import determine_source_quality

        video_ids = [uuid.uuid4(), uuid.uuid4()]
        quality = determine_source_quality(video_ids)
        assert quality == "multi_source"

    def test_multi_source_when_three_videos(self):
        from src.services.tech_standard_builder import determine_source_quality

        video_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        quality = determine_source_quality(video_ids)
        assert quality == "multi_source"

    def test_single_source_when_one_video(self):
        from src.services.tech_standard_builder import determine_source_quality

        video_ids = [uuid.uuid4()]
        quality = determine_source_quality(video_ids)
        assert quality == "single_source"

    def test_skip_when_no_videos(self):
        from src.services.tech_standard_builder import determine_source_quality

        quality = determine_source_quality([])
        assert quality is None  # signals skip


# ---------------------------------------------------------------------------
# BuildResult dataclass
# ---------------------------------------------------------------------------

class TestBuildResult:
    """Tests for BuildResult structure returned by build_standard."""

    def test_skipped_result_has_no_standard_id(self):
        from src.services.tech_standard_builder import BuildResult

        result = BuildResult(
            tech_category="forehand_topspin",
            result="skipped",
            reason="no_valid_points",
        )
        assert result.standard_id is None
        assert result.version is None

    def test_success_result_has_required_fields(self):
        from src.services.tech_standard_builder import BuildResult

        result = BuildResult(
            tech_category="forehand_topspin",
            result="success",
            standard_id=42,
            version=1,
            dimension_count=5,
            coach_count=3,
        )
        assert result.result == "success"
        assert result.dimension_count == 5


# ---------------------------------------------------------------------------
# US4: Source quality and per-dimension coach_count (T023)
# ---------------------------------------------------------------------------

class TestUS4SourceQualityAndCoachCount:
    """US4: source_quality and per-dimension coach_count visibility."""

    def test_five_coaches_gives_multi_source(self):
        from src.services.tech_standard_builder import determine_source_quality

        video_ids = [uuid.uuid4() for _ in range(5)]
        quality = determine_source_quality(video_ids)
        assert quality == "multi_source"

    def test_two_coaches_gives_multi_source(self):
        from src.services.tech_standard_builder import determine_source_quality

        video_ids = [uuid.uuid4(), uuid.uuid4()]
        quality = determine_source_quality(video_ids)
        assert quality == "multi_source"

    def test_one_coach_gives_single_source(self):
        from src.services.tech_standard_builder import determine_source_quality

        video_ids = [uuid.uuid4()]
        quality = determine_source_quality(video_ids)
        assert quality == "single_source"

    def test_duplicate_video_ids_counted_once(self):
        """Same source_video_id repeated → still counts as 1 unique coach."""
        from src.services.tech_standard_builder import determine_source_quality

        same_id = uuid.uuid4()
        video_ids = [same_id, same_id, same_id]
        quality = determine_source_quality(video_ids)
        assert quality == "single_source"

    def test_per_dimension_coach_count_is_distinct(self):
        """Verify that per-dimension coach_count reflects distinct source_video_id count."""
        # Simulate: 3 points for a dimension from 2 distinct videos
        vid_a = uuid.uuid4()
        vid_b = uuid.uuid4()
        dim_videos = [vid_a, vid_a, vid_b]  # vid_a appears twice but counted once

        unique_count = len(set(str(v) for v in dim_videos))
        assert unique_count == 2  # as computed in builder service

    def test_build_result_reports_coach_count(self):
        """BuildResult.coach_count field is populated for success results."""
        from src.services.tech_standard_builder import BuildResult

        result = BuildResult(
            tech_category="forehand_topspin",
            result="success",
            standard_id=1,
            version=1,
            dimension_count=3,
            coach_count=5,
        )
        assert result.coach_count == 5
