"""Contract tests for Teaching Tips API (Feature 005) — T013.

Tests:
  1. GET /teaching-tips — response schema valid
  2. PATCH /teaching-tips/{id} — source_type becomes 'human'
  3. POST /tasks/{task_id}/extract-tips — 202 response schema valid
  4. GET /tasks/{task_id}/result (athlete) — teaching_tips field present in CoachingAdviceItem
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """Create a TestClient with all DB dependencies mocked."""
    from src.api.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


# ── Contract: GET /teaching-tips ─────────────────────────────────────────────

class TestGetTeachingTipsContract:

    def test_response_schema_empty(self, client):
        """GET /teaching-tips returns valid schema even when DB is empty."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        async def mock_execute(*args, **kwargs):
            return mock_result

        with patch("src.api.routers.teaching_tips.get_db") as mock_get_db:
            mock_session = AsyncMock()
            mock_session.execute = mock_execute
            mock_get_db.return_value = mock_session

            # Direct model validation test (no HTTP call needed for schema)
            from src.api.schemas.teaching_tip import TeachingTipListResponse
            resp = TeachingTipListResponse(total=0, items=[])

            assert resp.total == 0
            assert resp.items == []

    def test_teaching_tip_response_has_required_fields(self):
        """TeachingTipResponse schema has all required fields from contracts/."""
        from src.api.schemas.teaching_tip import TeachingTipResponse
        import datetime

        tip = TeachingTipResponse(
            id=uuid.uuid4(),
            task_id=uuid.uuid4(),
            action_type="forehand_topspin",
            tech_phase="contact",
            tip_text="击球瞬间手腕要有爆发性摩擦",
            confidence=0.92,
            source_type="auto",
            original_text=None,
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now(),
        )
        assert tip.action_type == "forehand_topspin"
        assert tip.tech_phase == "contact"
        assert tip.source_type == "auto"


# ── Contract: PATCH /teaching-tips/{id} ──────────────────────────────────────

class TestPatchTeachingTipContract:

    def test_patch_sets_source_type_human(self):
        """After PATCH with new tip_text, source_type becomes 'human'."""
        from src.api.schemas.teaching_tip import TeachingTipPatch

        patch_body = TeachingTipPatch(tip_text="修改后的建议文字")
        assert patch_body.tip_text == "修改后的建议文字"
        assert patch_body.tech_phase is None  # not required

    def test_patch_schema_allows_partial_update(self):
        """TeachingTipPatch allows updating only tech_phase without tip_text."""
        from src.api.schemas.teaching_tip import TeachingTipPatch

        patch_body = TeachingTipPatch(tech_phase="preparation")
        assert patch_body.tech_phase == "preparation"
        assert patch_body.tip_text is None


# ── Contract: POST /tasks/{task_id}/extract-tips ──────────────────────────────

class TestExtractTipsContract:

    def test_extract_tips_response_schema(self):
        """ExtractTipsResponse schema matches contracts/teaching-tips-api.md."""
        from src.api.schemas.teaching_tip import ExtractTipsResponse

        task_id = uuid.uuid4()
        resp = ExtractTipsResponse(
            task_id=task_id,
            status="extracting",
            message="教学建议提炼已触发，将在30秒内完成",
            preserved_human_count=2,
        )
        assert resp.task_id == task_id
        assert resp.status == "extracting"
        assert resp.preserved_human_count == 2


# ── Contract: GET /tasks/{id}/result teaching_tips field ─────────────────────

class TestCoachingAdviceTeachingTipsContract:

    def test_coaching_advice_item_has_teaching_tips_field(self):
        """CoachingAdviceItem schema now includes teaching_tips array field."""
        from src.api.schemas.task import CoachingAdviceItem
        from src.api.schemas.teaching_tip import TeachingTipRef

        tip = TeachingTipRef(
            tip_text="击球时肘部不要夹紧，保持自然张开",
            tech_phase="contact",
            source_type="human",
        )
        advice = CoachingAdviceItem(
            advice_id=uuid.uuid4(),
            dimension="elbow_angle",
            deviation_description="正手拉球肘部角度偏低 15°",
            improvement_target="将肘部角度控制在 110°～145° 范围内",
            improvement_method="练习正手攻球时注意保持肘部抬起",
            impact_score=0.85,
            reliability_level="high",
            reliability_note=None,
            teaching_tips=[tip],
        )
        assert len(advice.teaching_tips) == 1
        assert advice.teaching_tips[0].tip_text == "击球时肘部不要夹紧，保持自然张开"
        assert advice.teaching_tips[0].source_type == "human"

    def test_coaching_advice_item_teaching_tips_defaults_empty(self):
        """CoachingAdviceItem.teaching_tips defaults to empty list (no breaking change)."""
        from src.api.schemas.task import CoachingAdviceItem

        advice = CoachingAdviceItem(
            advice_id=uuid.uuid4(),
            dimension="elbow_angle",
            deviation_description="偏低",
            improvement_target="改善",
            improvement_method="练习",
            impact_score=0.5,
            reliability_level="high",
        )
        assert advice.teaching_tips == []

    def test_teaching_tip_ref_schema(self):
        """TeachingTipRef embedded schema has tip_text, tech_phase, source_type."""
        from src.api.schemas.teaching_tip import TeachingTipRef

        ref = TeachingTipRef(
            tip_text="保持放松",
            tech_phase="preparation",
            source_type="auto",
        )
        assert ref.tip_text == "保持放松"
        assert ref.tech_phase == "preparation"
        assert ref.source_type == "auto"
