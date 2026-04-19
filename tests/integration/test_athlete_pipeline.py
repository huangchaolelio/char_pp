"""Athlete video pipeline integration test (T054).

Tests the athlete analysis pipeline:
  Upload → quality gate → KB version check → pose estimation →
  segmentation → deviation analysis → coaching advice generation

Uses mocked dependencies and pre-built KB fixtures.
"""

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def active_kb_fixture():
    """Fixture representing an already-active knowledge base version."""
    from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase
    from src.models.expert_tech_point import ExpertTechPoint, ActionType

    kb = MagicMock(spec=TechKnowledgeBase)
    kb.version = "1.0.0"
    kb.status = KBStatus.active

    ep = MagicMock(spec=ExpertTechPoint)
    ep.id = uuid.uuid4()
    ep.knowledge_base_version = "1.0.0"
    ep.action_type = ActionType.forehand_topspin
    ep.dimension = "elbow_angle"
    ep.param_min = 80.0
    ep.param_max = 110.0
    ep.param_ideal = 95.0
    ep.unit = "°"

    return kb, [ep]


@pytest.mark.integration
@pytest.mark.asyncio
class TestAthletePipeline:
    async def test_deviation_analysis_with_known_input(self, active_kb_fixture):
        """Given a known measured value, deviation is computed correctly."""
        from src.models.athlete_motion_analysis import AthleteMotionAnalysis, AthleteActionType
        from src.services.deviation_analyzer import analyze_deviations
        from src.models.deviation_report import DeviationDirection

        _, expert_points = active_kb_fixture

        session = AsyncMock()
        session.flush = AsyncMock()

        analysis = MagicMock(spec=AthleteMotionAnalysis)
        analysis.id = uuid.uuid4()
        analysis.overall_confidence = 0.85
        analysis.measured_params = {
            "elbow_angle": {"value": 125.0, "unit": "°", "confidence": 0.85}
        }

        reports = await analyze_deviations(session, analysis, expert_points)

        assert len(reports) == 1
        r = reports[0]
        assert r.deviation_direction == DeviationDirection.above
        assert abs(r.deviation_value - (125.0 - 95.0)) < 0.001
        assert r.is_low_confidence is False

    async def test_advice_generated_for_deviations(self, active_kb_fixture):
        """CoachingAdvice is generated for detected deviations."""
        from src.models.deviation_report import DeviationReport, DeviationDirection
        from src.services.advice_generator import generate_advice

        _, expert_points = active_kb_fixture
        ep = expert_points[0]

        session = AsyncMock()
        session.flush = AsyncMock()
        task_id = uuid.uuid4()

        deviation = MagicMock(spec=DeviationReport)
        deviation.id = uuid.uuid4()
        deviation.expert_point_id = ep.id
        deviation.dimension = "elbow_angle"
        deviation.deviation_direction = DeviationDirection.above
        deviation.confidence = 0.85
        deviation.impact_score = 0.7
        deviation.deviation_value = 30.0

        advice_list = await generate_advice(
            session=session,
            task_id=task_id,
            deviation_reports=[deviation],
            expert_points_by_id={ep.id: ep},
            action_type="forehand_topspin",
        )

        assert len(advice_list) == 1
        advice = advice_list[0]
        assert advice.task_id == task_id
        assert advice.deviation_id == deviation.id
        assert "elbow_angle" in advice.deviation_description or "肘部" in advice.deviation_description
        assert advice.impact_score == 0.7

    async def test_low_confidence_advice_has_note(self, active_kb_fixture):
        """Low confidence deviations produce advice with reliability_note."""
        from src.models.coaching_advice import ReliabilityLevel
        from src.models.deviation_report import DeviationReport, DeviationDirection
        from src.services.advice_generator import generate_advice

        _, expert_points = active_kb_fixture
        ep = expert_points[0]

        session = AsyncMock()
        session.flush = AsyncMock()
        task_id = uuid.uuid4()

        deviation = MagicMock(spec=DeviationReport)
        deviation.id = uuid.uuid4()
        deviation.expert_point_id = ep.id
        deviation.dimension = "elbow_angle"
        deviation.deviation_direction = DeviationDirection.below
        deviation.confidence = 0.55  # below threshold
        deviation.impact_score = 0.4
        deviation.deviation_value = -15.0

        advice_list = await generate_advice(
            session=session,
            task_id=task_id,
            deviation_reports=[deviation],
            expert_points_by_id={ep.id: ep},
            action_type="forehand_topspin",
        )

        assert len(advice_list) == 1
        assert advice_list[0].reliability_level == ReliabilityLevel.low
        assert advice_list[0].reliability_note is not None
        assert len(advice_list[0].reliability_note) > 0
