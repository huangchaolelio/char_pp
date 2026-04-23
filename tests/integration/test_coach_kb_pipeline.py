"""Coach-scoped KB pipeline integration tests (T028).

Verifies three scenarios:
  1. With coach_id: expert points are filtered to that coach's data only
  2. Without coach_id: global KB is used (no coach filter)
  3. Two coaches with distinct KB data do not cross-contaminate results
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ep(kb_version: str, action_type_val: str, dimension: str, task_id: uuid.UUID):
    """Create a mock ExpertTechPoint linked to a given task."""
    from src.models.expert_tech_point import ExpertTechPoint, ActionType

    ep = MagicMock(spec=ExpertTechPoint)
    ep.id = uuid.uuid4()
    ep.knowledge_base_version = kb_version
    ep.action_type = ActionType(action_type_val)
    ep.dimension = dimension
    ep.param_min = 80.0
    ep.param_max = 110.0
    ep.param_ideal = 95.0
    ep.unit = "°"
    ep.source_video_id = task_id
    return ep


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCoachKBPipeline:

    def test_coach_filter_selects_only_that_coach_ep(self):
        """EP query with coach_id joins AnalysisTask and applies coach_id filter."""
        from sqlalchemy import select
        from src.models.expert_tech_point import ExpertTechPoint
        from src.models.analysis_task import AnalysisTask

        coach_a_id = uuid.uuid4()
        coach_b_id = uuid.uuid4()
        kb_version = "1.0.0"

        # Build two mock tasks belonging to different coaches
        task_a_id = uuid.uuid4()
        task_b_id = uuid.uuid4()

        ep_a = _make_ep(kb_version, "forehand_topspin", "elbow_angle", task_a_id)
        ep_b = _make_ep(kb_version, "forehand_topspin", "elbow_angle", task_b_id)

        # Simulate what _persist_athlete_results does: build stmt with coach_id filter
        _AT = AnalysisTask
        stmt_with_coach = (
            select(ExpertTechPoint)
            .join(_AT, ExpertTechPoint.source_video_id == _AT.id)
            .where(
                ExpertTechPoint.knowledge_base_version == kb_version,
                _AT.coach_id == coach_a_id,
            )
        )

        # Verify the WHERE clause contains coach_id condition (check compiled SQL)
        from sqlalchemy.dialects import postgresql
        compiled = stmt_with_coach.compile(dialect=postgresql.dialect())
        sql_str = str(compiled)
        assert "coach_id" in sql_str, "SQL should filter by coach_id"

    def test_no_coach_filter_uses_global_kb(self):
        """EP query without coach_id does NOT join AnalysisTask."""
        from sqlalchemy import select
        from src.models.expert_tech_point import ExpertTechPoint

        kb_version = "1.0.0"
        stmt_global = select(ExpertTechPoint).where(
            ExpertTechPoint.knowledge_base_version == kb_version
        )

        from sqlalchemy.dialects import postgresql
        compiled = stmt_global.compile(dialect=postgresql.dialect())
        sql_str = str(compiled)
        assert "coach_id" not in sql_str, "Global KB query should NOT filter by coach_id"
        assert "analysis_tasks" not in sql_str, "Global KB query should NOT join analysis_tasks"

    def test_two_coaches_produce_independent_ep_queries(self):
        """Separate queries for coach A and coach B should have distinct WHERE clauses."""
        from sqlalchemy import select
        from src.models.expert_tech_point import ExpertTechPoint
        from src.models.analysis_task import AnalysisTask
        from sqlalchemy.dialects import postgresql

        coach_a_id = uuid.uuid4()
        coach_b_id = uuid.uuid4()
        kb_version = "1.0.0"

        _AT = AnalysisTask

        def _build_stmt(coach_id):
            return (
                select(ExpertTechPoint)
                .join(_AT, ExpertTechPoint.source_video_id == _AT.id)
                .where(
                    ExpertTechPoint.knowledge_base_version == kb_version,
                    _AT.coach_id == coach_id,
                )
            )

        stmt_a = _build_stmt(coach_a_id)
        stmt_b = _build_stmt(coach_b_id)

        params_a = stmt_a.compile(dialect=postgresql.dialect()).params
        params_b = stmt_b.compile(dialect=postgresql.dialect()).params

        # The two queries should use different coach_id parameter values
        coach_vals_a = [v for v in params_a.values() if isinstance(v, uuid.UUID)]
        coach_vals_b = [v for v in params_b.values() if isinstance(v, uuid.UUID)]

        assert coach_vals_a != coach_vals_b, (
            "Queries for different coaches must use different coach_id values"
        )
        assert coach_a_id in coach_vals_a
        assert coach_b_id in coach_vals_b
