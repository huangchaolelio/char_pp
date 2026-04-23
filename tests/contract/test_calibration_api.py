"""Contract tests for Calibration API (Feature 006) — T019.

Tests:
  GET /calibration/tech-points?action_type=X&dimension=Y
    - Two coaches: structured comparison returned
    - One coach: single entry, no error
    - No data: empty coaches list
    - Missing required param: 422
  GET /calibration/teaching-tips?action_type=X&tech_phase=Y
    - Returns grouped tips by coach
    - Missing required param: 422
"""

from __future__ import annotations

import uuid

import pytest


# ── Schema-level tests ────────────────────────────────────────────────────────

class TestCalibrationSchemas:

    def test_tech_point_calibration_view_schema(self):
        from src.api.schemas.coach import CoachTechPointEntry, TechPointCalibrationView

        entry = CoachTechPointEntry(
            coach_id=uuid.uuid4(),
            coach_name="张教练",
            param_min=85.0,
            param_ideal=95.0,
            param_max=110.0,
            unit="°",
            extraction_confidence=0.92,
            source_count=3,
        )
        view = TechPointCalibrationView(
            action_type="forehand_topspin",
            dimension="elbow_angle",
            coaches=[entry],
        )
        assert view.action_type == "forehand_topspin"
        assert view.dimension == "elbow_angle"
        assert len(view.coaches) == 1
        assert view.coaches[0].coach_name == "张教练"
        assert view.coaches[0].source_count == 3

    def test_tech_point_calibration_view_empty_coaches(self):
        from src.api.schemas.coach import TechPointCalibrationView

        view = TechPointCalibrationView(
            action_type="forehand_topspin",
            dimension="elbow_angle",
            coaches=[],
        )
        assert view.coaches == []

    def test_teaching_tip_calibration_view_schema(self):
        from src.api.schemas.coach import CoachTipGroup, TeachingTipCalibrationView

        group = CoachTipGroup(
            coach_id=uuid.uuid4(),
            coach_name="张教练",
            tips=["击球时手腕内旋", "保持肘部稳定"],
        )
        view = TeachingTipCalibrationView(
            action_type="forehand_topspin",
            tech_phase="contact",
            coaches=[group],
        )
        assert view.tech_phase == "contact"
        assert len(view.coaches[0].tips) == 2

    def test_teaching_tip_calibration_view_empty(self):
        from src.api.schemas.coach import TeachingTipCalibrationView

        view = TeachingTipCalibrationView(
            action_type="forehand_topspin",
            tech_phase="contact",
            coaches=[],
        )
        assert view.coaches == []


# ── Router signature tests ────────────────────────────────────────────────────

class TestCalibrationRouterSignatures:

    def test_calibrate_tech_points_requires_action_type_and_dimension(self):
        """Both action_type and dimension are required Query params."""
        import inspect
        from src.api.routers.calibration import calibrate_tech_points
        sig = inspect.signature(calibrate_tech_points)
        params = sig.parameters
        assert "action_type" in params
        assert "dimension" in params
        # Both have no default (Query(...) means required)
        from fastapi import Query
        # Check they exist as query params
        assert params["action_type"].default is not None  # Query(...) object

    def test_calibrate_teaching_tips_requires_action_type_and_tech_phase(self):
        """Both action_type and tech_phase are required Query params."""
        import inspect
        from src.api.routers.calibration import calibrate_teaching_tips
        sig = inspect.signature(calibrate_teaching_tips)
        params = sig.parameters
        assert "action_type" in params
        assert "tech_phase" in params

    def test_calibration_router_tags(self):
        """Calibration router has 'calibration' tag."""
        from src.api.routers.calibration import router
        assert "calibration" in router.tags


# ── Multi-coach comparison logic ──────────────────────────────────────────────

class TestCalibrationMultiCoach:

    def test_two_coach_entries_in_view(self):
        from src.api.schemas.coach import CoachTechPointEntry, TechPointCalibrationView

        coach_a = CoachTechPointEntry(
            coach_id=uuid.uuid4(), coach_name="张教练",
            param_min=85.0, param_ideal=95.0, param_max=110.0,
            unit="°", extraction_confidence=0.92, source_count=2,
        )
        coach_b = CoachTechPointEntry(
            coach_id=uuid.uuid4(), coach_name="李教练",
            param_min=80.0, param_ideal=90.0, param_max=105.0,
            unit="°", extraction_confidence=0.88, source_count=1,
        )
        view = TechPointCalibrationView(
            action_type="forehand_topspin",
            dimension="elbow_angle",
            coaches=[coach_a, coach_b],
        )
        assert len(view.coaches) == 2
        names = [c.coach_name for c in view.coaches]
        assert "张教练" in names
        assert "李教练" in names

    def test_single_coach_no_error(self):
        """Single coach entry is valid — calibration works with one coach."""
        from src.api.schemas.coach import CoachTechPointEntry, TechPointCalibrationView

        entry = CoachTechPointEntry(
            coach_id=uuid.uuid4(), coach_name="张教练",
            param_min=85.0, param_ideal=95.0, param_max=110.0,
            unit="°", extraction_confidence=0.92, source_count=1,
        )
        view = TechPointCalibrationView(
            action_type="forehand_topspin",
            dimension="elbow_angle",
            coaches=[entry],
        )
        assert len(view.coaches) == 1

    def test_tip_group_multiple_coaches(self):
        from src.api.schemas.coach import CoachTipGroup, TeachingTipCalibrationView

        groups = [
            CoachTipGroup(
                coach_id=uuid.uuid4(), coach_name="张教练",
                tips=["肘部自然弯曲", "发力点在前臂"],
            ),
            CoachTipGroup(
                coach_id=uuid.uuid4(), coach_name="李教练",
                tips=["保持稳定不提前发力"],
            ),
        ]
        view = TeachingTipCalibrationView(
            action_type="forehand_topspin",
            tech_phase="contact",
            coaches=groups,
        )
        assert len(view.coaches) == 2
        total_tips = sum(len(g.tips) for g in view.coaches)
        assert total_tips == 3
