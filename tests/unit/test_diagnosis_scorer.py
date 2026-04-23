"""Unit tests for diagnosis_scorer — pure function scoring logic.

Tests cover:
  T007(a) - value within [min, max] → ok, score=100, direction=none
  T007(b) - slight deviation (1x to 1.5x half-width) → linear [60, 100)
  T007(c) - significant deviation (> 1.5x half-width) → linear [0, 60)
  T007(d) - direction: above/below/none
  T007(e) - overall_score = simple mean of dimension scores
  T007(f) - empty dimension list → overall_score = 0
  T007(g) - zero half-width (min == max) edge case
  T018(a) - boundary: measured == min or max → ok
  T018(b) - boundary at exactly 1.5x half-width
  T018(c) - deviation_direction set correctly for all levels
"""

from __future__ import annotations

import pytest

from src.services.diagnosis_scorer import (
    DeviationDirection,
    DeviationLevel,
    DimensionScore,
    compute_dimension_score,
    compute_overall_score,
)


# ---------------------------------------------------------------------------
# T007(a) — within range: ok, score=100
# ---------------------------------------------------------------------------

class TestWithinRange:
    def test_at_ideal_value(self):
        ds = compute_dimension_score(95.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_level == DeviationLevel.ok
        assert ds.score == pytest.approx(100.0)
        assert ds.deviation_direction == DeviationDirection.none

    def test_at_min_boundary(self):
        """T018(a): measured == min → still ok"""
        ds = compute_dimension_score(85.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_level == DeviationLevel.ok
        assert ds.score == pytest.approx(100.0)

    def test_at_max_boundary(self):
        """T018(a): measured == max → still ok"""
        ds = compute_dimension_score(105.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_level == DeviationLevel.ok
        assert ds.score == pytest.approx(100.0)

    def test_near_min(self):
        ds = compute_dimension_score(86.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_level == DeviationLevel.ok
        assert ds.score == pytest.approx(100.0)
        assert ds.deviation_direction == DeviationDirection.none

    def test_near_max(self):
        ds = compute_dimension_score(104.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_level == DeviationLevel.ok
        assert ds.score == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# T007(b) — slight deviation: score linearly in [60, 100)
# ---------------------------------------------------------------------------

class TestSlightDeviation:
    def test_just_above_max(self):
        """Measured just above max → slight, score < 100"""
        # min=85, max=105 → half_width=10, center=95
        # measured=108 → distance=13 (> 10, < 15=1.5*hw) → slight
        ds = compute_dimension_score(108.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_level == DeviationLevel.slight
        assert 60.0 <= ds.score < 100.0
        assert ds.deviation_direction == DeviationDirection.above

    def test_just_below_min(self):
        """Measured just below min → slight, score < 100"""
        # measured=82 → distance=13 → slight
        ds = compute_dimension_score(82.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_level == DeviationLevel.slight
        assert 60.0 <= ds.score < 100.0
        assert ds.deviation_direction == DeviationDirection.below

    def test_at_15x_boundary(self):
        """T018(b): measured exactly at 1.5x half-width boundary"""
        # min=85, max=105 → half_width=10, 1.5*hw=15
        # measured = 95 + 15 = 110 → boundary between slight and significant
        # At exactly boundary, plan says slight/significant switch correctly
        ds = compute_dimension_score(110.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        # Boundary point: score should be 60 (boundary value)
        assert ds.score == pytest.approx(60.0, abs=1.0)

    def test_score_increases_toward_range(self):
        """Score should be higher when closer to range boundary"""
        ds_close = compute_dimension_score(106.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        ds_far = compute_dimension_score(109.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds_close.score > ds_far.score


# ---------------------------------------------------------------------------
# T007(c) — significant deviation: score in [0, 60)
# ---------------------------------------------------------------------------

class TestSignificantDeviation:
    def test_far_above_max(self):
        """Measured far above max → significant, score < 60"""
        # measured=115 → distance=20 > 1.5*10=15 → significant
        ds = compute_dimension_score(115.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_level == DeviationLevel.significant
        assert 0.0 <= ds.score < 60.0
        assert ds.deviation_direction == DeviationDirection.above

    def test_far_below_min(self):
        """Measured far below min → significant"""
        # measured=70 → distance=25 > 15 → significant
        ds = compute_dimension_score(70.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_level == DeviationLevel.significant
        assert 0.0 <= ds.score < 60.0
        assert ds.deviation_direction == DeviationDirection.below

    def test_score_decreases_with_distance(self):
        """Further deviations should have lower scores"""
        ds_near = compute_dimension_score(115.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        ds_far = compute_dimension_score(130.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds_near.score >= ds_far.score

    def test_score_never_negative(self):
        """Score must not go below 0 even for extreme deviations"""
        ds = compute_dimension_score(1000.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.score >= 0.0


# ---------------------------------------------------------------------------
# T007(d) — deviation direction
# ---------------------------------------------------------------------------

class TestDeviationDirection:
    def test_within_range_is_none(self):
        ds = compute_dimension_score(95.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_direction == DeviationDirection.none

    def test_above_max_is_above(self):
        ds = compute_dimension_score(120.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_direction == DeviationDirection.above

    def test_below_min_is_below(self):
        ds = compute_dimension_score(50.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_direction == DeviationDirection.below

    def test_direction_set_for_slight_above(self):
        """T018(c): direction correct even for slight deviation"""
        ds = compute_dimension_score(108.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_direction == DeviationDirection.above

    def test_direction_set_for_significant_below(self):
        """T018(c): direction correct for significant below"""
        ds = compute_dimension_score(70.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.deviation_direction == DeviationDirection.below


# ---------------------------------------------------------------------------
# T007(e) — overall_score = mean
# ---------------------------------------------------------------------------

class TestOverallScore:
    def test_all_ok_gives_100(self):
        dims = [
            compute_dimension_score(95.0, 85.0, 105.0, 95.0, "°", "d1"),
            compute_dimension_score(0.65, 0.55, 0.80, 0.65, "ratio", "d2"),
        ]
        assert compute_overall_score(dims) == pytest.approx(100.0)

    def test_mixed_scores_averaged(self):
        dim_ok = compute_dimension_score(95.0, 85.0, 105.0, 95.0, "°", "d1")
        dim_bad = compute_dimension_score(70.0, 85.0, 105.0, 95.0, "°", "d2")
        overall = compute_overall_score([dim_ok, dim_bad])
        expected = (dim_ok.score + dim_bad.score) / 2
        assert overall == pytest.approx(expected, abs=0.01)

    def test_single_dimension(self):
        dim = compute_dimension_score(108.0, 85.0, 105.0, 95.0, "°", "d1")
        assert compute_overall_score([dim]) == pytest.approx(dim.score)

    def test_five_dimensions(self):
        dims = [
            compute_dimension_score(95.0, 85.0, 105.0, 95.0, "°", f"d{i}")
            for i in range(5)
        ]
        assert compute_overall_score(dims) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# T007(f) — empty list → 0
# ---------------------------------------------------------------------------

class TestEmptyDimensions:
    def test_empty_list_returns_zero(self):
        assert compute_overall_score([]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# T007(g) — zero half-width (min == max)
# ---------------------------------------------------------------------------

class TestZeroHalfWidth:
    def test_min_equals_max_measured_at_value(self):
        """When min==max, measured at exact value → ok"""
        ds = compute_dimension_score(45.0, 45.0, 45.0, 45.0, "°", "hip_rotation")
        assert ds.deviation_level == DeviationLevel.ok
        assert ds.score == pytest.approx(100.0)

    def test_min_equals_max_measured_different(self):
        """When min==max, measured differs → does not raise ZeroDivisionError"""
        ds = compute_dimension_score(50.0, 45.0, 45.0, 45.0, "°", "hip_rotation")
        # Should not raise, score should be valid float
        assert isinstance(ds.score, float)
        assert ds.score >= 0.0

    def test_min_equals_max_significant(self):
        """When min==max and measured far from it → significant"""
        ds = compute_dimension_score(100.0, 45.0, 45.0, 45.0, "°", "hip_rotation")
        assert ds.deviation_level == DeviationLevel.significant
        assert ds.deviation_direction == DeviationDirection.above


# ---------------------------------------------------------------------------
# DimensionScore dataclass fields
# ---------------------------------------------------------------------------

class TestDimensionScoreFields:
    def test_fields_populated(self):
        ds = compute_dimension_score(95.0, 85.0, 105.0, 95.0, "°", "elbow_angle")
        assert ds.dimension == "elbow_angle"
        assert ds.measured_value == pytest.approx(95.0)
        assert ds.ideal_value == pytest.approx(95.0)
        assert ds.standard_min == pytest.approx(85.0)
        assert ds.standard_max == pytest.approx(105.0)
        assert ds.unit == "°"
