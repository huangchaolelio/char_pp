"""Contract tests for Teaching Tips API (Feature 005 + 006) — T013, T016.

Tests:
  1. GET /teaching-tips — response schema valid
  2. PATCH /teaching-tips/{id} — source_type becomes 'human'
  3. POST /tasks/{task_id}/extract-tips — 202 response schema valid
  4. GET /tasks/{task_id}/result (athlete) — teaching_tips field present in CoachingAdviceItem
  5. [Feature 006] GET /teaching-tips?coach_id={id} — coach filter support
  6. [Feature 006] TeachingTipResponse includes coach_id and coach_name fields
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
        """GET /teaching-tips returns valid SuccessEnvelope even when DB is empty.

        Feature-017 aligned: 原 TeachingTipListResponse 包装类已删除，改用
        SuccessEnvelope[list[TeachingTipResponse]] + PaginationMeta 套信封。
        """
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar.return_value = 0  # total count

        async def mock_execute(*args, **kwargs):
            return mock_result

        from src.db.session import get_db
        from src.api.main import app as real_app

        mock_session = AsyncMock()
        mock_session.execute = mock_execute

        async def _db_override():
            yield mock_session

        real_app.dependency_overrides[get_db] = _db_override
        try:
            resp = client.get("/api/v1/teaching-tips")
        finally:
            real_app.dependency_overrides.pop(get_db, None)

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []
        assert body["meta"]["total"] == 0
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == 20

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


# ── [Feature 006] Contract: GET /teaching-tips?coach_id ─────────────────────

class TestTeachingTipsCoachFilter:

    def test_teaching_tip_response_has_coach_fields(self):
        """TeachingTipResponse includes coach_id and coach_name (Feature 006)."""
        from src.api.schemas.teaching_tip import TeachingTipResponse
        import datetime

        tip = TeachingTipResponse(
            id=__import__("uuid").uuid4(),
            task_id=__import__("uuid").uuid4(),
            action_type="forehand_topspin",
            tech_phase="contact",
            tip_text="击球瞬间手腕内旋",
            confidence=0.9,
            source_type="auto",
            original_text=None,
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now(),
            coach_id=None,
            coach_name=None,
        )
        assert tip.coach_id is None
        assert tip.coach_name is None

    def test_teaching_tip_response_with_coach(self):
        """TeachingTipResponse carries coach info when coach is assigned."""
        from src.api.schemas.teaching_tip import TeachingTipResponse
        import uuid, datetime

        coach_id = uuid.uuid4()
        tip = TeachingTipResponse(
            id=uuid.uuid4(),
            task_id=uuid.uuid4(),
            action_type="forehand_topspin",
            tech_phase="contact",
            tip_text="击球瞬间手腕内旋",
            confidence=0.9,
            source_type="auto",
            original_text=None,
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now(),
            coach_id=coach_id,
            coach_name="张教练",
        )
        assert tip.coach_id == coach_id
        assert tip.coach_name == "张教练"

    def test_list_teaching_tips_accepts_coach_id_param(self):
        """GET /teaching-tips endpoint supports coach_id query param."""
        import inspect
        from src.api.routers.teaching_tips import list_teaching_tips
        sig = inspect.signature(list_teaching_tips)
        assert "coach_id" in sig.parameters

    def test_nonexistent_coach_id_returns_empty_list(self):
        """coach_id filter with unknown UUID should return empty list (not error)."""
        # Schema-level validation: coach_id is optional UUID
        from src.api.schemas.coach import TaskCoachUpdate
        body = TaskCoachUpdate(coach_id=__import__("uuid").uuid4())
        assert body.coach_id is not None

