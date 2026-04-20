"""Unit tests for advice generator (T051).

Tests:
  - Each deviation with direction ≠ none generates one advice record
  - reliability_level high/low branches based on confidence threshold
  - Output sorted by impact_score DESC
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.coaching_advice import ReliabilityLevel
from src.models.deviation_report import DeviationDirection, DeviationReport
from src.models.expert_tech_point import ExpertTechPoint
from src.services.advice_generator import generate_advice


def _make_report(
    dimension: str,
    direction: DeviationDirection,
    confidence: float,
    impact_score: float,
    deviation_value: float = 10.0,
) -> DeviationReport:
    report = MagicMock(spec=DeviationReport)
    report.id = uuid.uuid4()
    report.dimension = dimension
    report.deviation_direction = direction
    report.confidence = confidence
    report.impact_score = impact_score
    report.deviation_value = deviation_value
    return report


def _make_expert_point(dimension: str) -> ExpertTechPoint:
    ep = MagicMock(spec=ExpertTechPoint)
    ep.id = uuid.uuid4()
    ep.dimension = dimension
    ep.param_min = 80.0
    ep.param_max = 110.0
    ep.param_ideal = 95.0
    ep.unit = "°"
    return ep


def _make_session_with_empty_tips() -> AsyncMock:
    """Create a mock session that returns an empty TeachingTip result."""
    session = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()

    # Mock execute to return an empty scalars result for TeachingTip query
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_execute_result)
    return session


@pytest.mark.unit
@pytest.mark.asyncio
class TestGenerateAdvice:
    async def test_generates_advice_for_each_deviation(self):
        session = _make_session_with_empty_tips()
        task_id = uuid.uuid4()

        elbow_ep = _make_expert_point("elbow_angle")
        wt_ep = _make_expert_point("weight_transfer")
        wt_ep.unit = "ratio"
        wt_ep.param_ideal = 0.5

        reports = [
            _make_report("elbow_angle", DeviationDirection.above, 0.85, 0.8),
            _make_report("weight_transfer", DeviationDirection.below, 0.75, 0.5),
        ]
        ep_by_id = {elbow_ep.id: elbow_ep, wt_ep.id: wt_ep}
        reports[0].expert_point_id = elbow_ep.id
        reports[1].expert_point_id = wt_ep.id

        advice = await generate_advice(session, task_id, reports, ep_by_id, "forehand_topspin")

        assert len(advice) == 2

    async def test_skips_none_direction(self):
        session = _make_session_with_empty_tips()
        task_id = uuid.uuid4()

        ep = _make_expert_point("elbow_angle")
        reports = [
            _make_report("elbow_angle", DeviationDirection.none, 0.85, 0.0),
        ]
        reports[0].expert_point_id = ep.id

        advice = await generate_advice(session, task_id, reports, {ep.id: ep}, "forehand_topspin")
        assert len(advice) == 0

    async def test_high_reliability_for_high_confidence(self):
        session = _make_session_with_empty_tips()
        task_id = uuid.uuid4()

        ep = _make_expert_point("elbow_angle")
        reports = [_make_report("elbow_angle", DeviationDirection.above, 0.9, 0.6)]
        reports[0].expert_point_id = ep.id

        advice = await generate_advice(session, task_id, reports, {ep.id: ep}, "forehand_topspin")
        assert advice[0].reliability_level == ReliabilityLevel.high
        assert advice[0].reliability_note is None

    async def test_low_reliability_for_low_confidence(self):
        session = _make_session_with_empty_tips()
        task_id = uuid.uuid4()

        ep = _make_expert_point("elbow_angle")
        reports = [_make_report("elbow_angle", DeviationDirection.above, 0.6, 0.4)]
        reports[0].expert_point_id = ep.id

        advice = await generate_advice(session, task_id, reports, {ep.id: ep}, "forehand_topspin")
        assert advice[0].reliability_level == ReliabilityLevel.low
        assert advice[0].reliability_note is not None
        assert len(advice[0].reliability_note) > 0

    async def test_sorted_by_impact_score_desc(self):
        session = _make_session_with_empty_tips()
        task_id = uuid.uuid4()

        ep1 = _make_expert_point("elbow_angle")
        ep2 = _make_expert_point("swing_trajectory")
        ep2.unit = "ratio"

        reports = [
            _make_report("elbow_angle", DeviationDirection.above, 0.8, 0.3),
            _make_report("swing_trajectory", DeviationDirection.below, 0.8, 0.9),
        ]
        reports[0].expert_point_id = ep1.id
        reports[1].expert_point_id = ep2.id

        advice = await generate_advice(
            session, task_id, reports,
            {ep1.id: ep1, ep2.id: ep2},
            "forehand_topspin",
        )
        assert len(advice) == 2
        assert advice[0].impact_score >= advice[1].impact_score

