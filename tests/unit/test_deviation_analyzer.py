"""Unit tests for deviation analyzer (T050).

Tests:
  - Known input → correct deviation_value, direction, impact_score
  - confidence < 0.7 → is_low_confidence=True
  - Stability aggregation: sample < 3 → None; ≥3 with ≥70% rate → True; else False
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.deviation_report import DeviationDirection
from src.services.deviation_analyzer import (
    _compute_direction,
    _compute_impact,
    analyze_deviations,
    compute_stability,
)


@pytest.mark.unit
class TestComputeDirection:
    def test_above_when_measured_exceeds_max(self):
        assert _compute_direction(120.0, 80.0, 110.0) == DeviationDirection.above

    def test_below_when_measured_under_min(self):
        assert _compute_direction(70.0, 80.0, 110.0) == DeviationDirection.below

    def test_none_when_within_range(self):
        assert _compute_direction(95.0, 80.0, 110.0) == DeviationDirection.none

    def test_none_when_exactly_at_min(self):
        assert _compute_direction(80.0, 80.0, 110.0) == DeviationDirection.none

    def test_none_when_exactly_at_max(self):
        assert _compute_direction(110.0, 80.0, 110.0) == DeviationDirection.none


@pytest.mark.unit
class TestComputeImpact:
    def test_normalized_impact(self):
        # deviation_value = 20.0, param range = 30.0, impact = 20/30 ≈ 0.667
        score = _compute_impact(20.0, 80.0, 110.0)
        assert abs(score - 2/3) < 0.001

    def test_impact_clamped_to_one(self):
        # Large deviation beyond param range should be clamped to 1.0
        score = _compute_impact(100.0, 80.0, 110.0)
        assert score == 1.0

    def test_zero_range_fallback(self):
        # If param_min == param_max, use clamped abs deviation
        score = _compute_impact(0.5, 90.0, 90.0)
        assert 0.0 <= score <= 1.0


@pytest.mark.unit
@pytest.mark.asyncio
class TestAnalyzeDeviations:
    async def test_creates_deviation_for_each_dimension(self):
        from src.models.athlete_motion_analysis import AthleteMotionAnalysis, AthleteActionType
        from src.models.expert_tech_point import ExpertTechPoint, ActionType

        session = AsyncMock()
        session.flush = AsyncMock()

        analysis = MagicMock(spec=AthleteMotionAnalysis)
        analysis.id = uuid.uuid4()
        analysis.overall_confidence = 0.85
        analysis.measured_params = {
            "elbow_angle": {"value": 120.0, "unit": "°", "confidence": 0.85}
        }

        ep = MagicMock(spec=ExpertTechPoint)
        ep.id = uuid.uuid4()
        ep.dimension = "elbow_angle"
        ep.param_min = 80.0
        ep.param_max = 110.0
        ep.param_ideal = 95.0

        reports = await analyze_deviations(session, analysis, [ep])

        assert len(reports) == 1
        r = reports[0]
        assert r.dimension == "elbow_angle"
        assert abs(r.deviation_value - (120.0 - 95.0)) < 0.001
        assert r.deviation_direction == DeviationDirection.above
        assert r.is_low_confidence is False
        assert r.impact_score is not None

    async def test_low_confidence_flag_set_correctly(self):
        from src.models.athlete_motion_analysis import AthleteMotionAnalysis
        from src.models.expert_tech_point import ExpertTechPoint

        session = AsyncMock()
        session.flush = AsyncMock()

        analysis = MagicMock(spec=AthleteMotionAnalysis)
        analysis.id = uuid.uuid4()
        analysis.overall_confidence = 0.5
        analysis.measured_params = {
            "elbow_angle": {"value": 75.0, "unit": "°", "confidence": 0.6}
        }

        ep = MagicMock(spec=ExpertTechPoint)
        ep.id = uuid.uuid4()
        ep.dimension = "elbow_angle"
        ep.param_min = 80.0
        ep.param_max = 110.0
        ep.param_ideal = 95.0

        reports = await analyze_deviations(session, analysis, [ep])
        assert reports[0].is_low_confidence is True

    async def test_skips_dimension_without_measured_value(self):
        from src.models.athlete_motion_analysis import AthleteMotionAnalysis
        from src.models.expert_tech_point import ExpertTechPoint

        session = AsyncMock()
        session.flush = AsyncMock()

        analysis = MagicMock(spec=AthleteMotionAnalysis)
        analysis.id = uuid.uuid4()
        analysis.overall_confidence = 0.9
        analysis.measured_params = {}  # no measurements

        ep = MagicMock(spec=ExpertTechPoint)
        ep.id = uuid.uuid4()
        ep.dimension = "elbow_angle"
        ep.param_min = 80.0
        ep.param_max = 110.0
        ep.param_ideal = 95.0

        reports = await analyze_deviations(session, analysis, [ep])
        assert len(reports) == 0


@pytest.mark.unit
@pytest.mark.asyncio
class TestComputeStability:
    async def test_returns_none_when_insufficient_samples(self):
        session = AsyncMock()
        # Empty analysis IDs
        result = await compute_stability(session, [], "forehand_topspin", "elbow_angle")
        assert result is None

    async def test_returns_none_when_fewer_than_3_reports(self):
        from sqlalchemy.engine.result import ScalarResult
        from unittest.mock import MagicMock

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            MagicMock(deviation_direction=DeviationDirection.above),
            MagicMock(deviation_direction=DeviationDirection.above),
        ]
        session.execute = AsyncMock(return_value=mock_result)

        ids = [uuid.uuid4(), uuid.uuid4()]
        result = await compute_stability(session, ids, "forehand_topspin", "elbow_angle")
        assert result is None

    async def test_returns_true_when_stable(self):
        session = AsyncMock()
        mock_result = MagicMock()
        # 4 samples, 3 deviate = 75% >= 70%
        mock_result.scalars.return_value.all.return_value = [
            MagicMock(deviation_direction=DeviationDirection.above),
            MagicMock(deviation_direction=DeviationDirection.above),
            MagicMock(deviation_direction=DeviationDirection.above),
            MagicMock(deviation_direction=DeviationDirection.none),
        ]
        session.execute = AsyncMock(return_value=mock_result)

        ids = [uuid.uuid4() for _ in range(4)]
        result = await compute_stability(session, ids, "forehand_topspin", "elbow_angle")
        assert result is True

    async def test_returns_false_when_not_stable(self):
        session = AsyncMock()
        mock_result = MagicMock()
        # 4 samples, 2 deviate = 50% < 70%
        mock_result.scalars.return_value.all.return_value = [
            MagicMock(deviation_direction=DeviationDirection.above),
            MagicMock(deviation_direction=DeviationDirection.above),
            MagicMock(deviation_direction=DeviationDirection.none),
            MagicMock(deviation_direction=DeviationDirection.none),
        ]
        session.execute = AsyncMock(return_value=mock_result)

        ids = [uuid.uuid4() for _ in range(4)]
        result = await compute_stability(session, ids, "forehand_topspin", "elbow_angle")
        assert result is False
