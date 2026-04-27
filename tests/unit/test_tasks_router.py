"""Unit tests for tasks router — US1 endpoints (T025–T027, T030).

Feature-017: POST /tasks/expert-video 已下线（替代 /tasks/classification + /tasks/kb-extraction），
原 `TestSubmitExpertVideo` 测试类已移除。
"""
import uuid
from datetime import datetime, timezone as _tz
UTC = _tz.utc
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit.conftest import COS_KEY, KB_VERSION, TASK_ID, make_kb, make_task, make_tech_point


# ── GET /api/v1/tasks/{task_id} ─────────────────────────────────────
class TestGetTaskStatus:
    @pytest.mark.asyncio
    async def test_invalid_uuid_returns_404(self, client, override_db):
        resp = await client.get("/api/v1/tasks/not-a-uuid")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "TASK_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_task_not_found_returns_404(self, client, override_db):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        override_db.execute = AsyncMock(return_value=result)

        resp = await client.get(f"/api/v1/tasks/{TASK_ID}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types")
    async def test_returns_task_status(self, client, override_db):
        task = make_task(status="processing")
        result = MagicMock()
        result.scalar_one_or_none.return_value = task
        override_db.execute = AsyncMock(return_value=result)

        resp = await client.get(f"/api/v1/tasks/{TASK_ID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == str(TASK_ID)
        assert body["status"] == "processing"
        assert body["task_type"] == "expert_video"

    @pytest.mark.asyncio
    async def test_soft_deleted_task_returns_404(self, client, override_db):
        # Simulate DB returning None (deleted_at filter applied in WHERE clause)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        override_db.execute = AsyncMock(return_value=result)

        resp = await client.get(f"/api/v1/tasks/{TASK_ID}")
        assert resp.status_code == 404


# ── GET /api/v1/tasks/{task_id}/result ───────────────────────────────────────

class TestGetTaskResult:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types")
    async def test_task_not_ready_returns_409(self, client, override_db):
        task = make_task(status="processing")
        result = MagicMock()
        result.scalar_one_or_none.return_value = task
        override_db.execute = AsyncMock(return_value=result)

        resp = await client.get(f"/api/v1/tasks/{TASK_ID}/result")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "TASK_NOT_READY"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types")
    async def test_expert_video_success_returns_points(self, client, override_db):
        task = make_task(status="success", task_type="expert_video", kb_version=KB_VERSION)
        kb = make_kb(version=KB_VERSION, status="draft")
        point = make_tech_point(version=KB_VERSION)

        task_result = MagicMock()
        task_result.scalar_one_or_none.return_value = task

        points_result = MagicMock()
        points_result.all.return_value = [(point, None)]  # outerjoin returns (ExpertTechPoint, TechSemanticSegment) tuples

        kb_result = MagicMock()
        kb_result.scalar_one_or_none.return_value = kb

        # Feature 002: 4th execute call → AudioTranscript lookup (returns None)
        audio_result = MagicMock()
        audio_result.scalar_one_or_none.return_value = None

        override_db.execute = AsyncMock(
            side_effect=[task_result, points_result, kb_result, audio_result]
        )

        resp = await client.get(f"/api/v1/tasks/{TASK_ID}/result")
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == str(TASK_ID)
        assert body["knowledge_base_version_draft"] == KB_VERSION
        assert body["extracted_points_count"] == 1
        assert body["pending_approval"] is True
        assert body["extracted_points"][0]["dimension"] == "elbow_angle"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types")
    async def test_athlete_video_result_no_analysis(self, client, override_db):
        """Athlete task with no motion analysis yet returns 200 with empty deviations."""
        task = make_task(status="success", task_type="athlete_video")
        # execute calls:
        # 1st: task lookup
        # 2nd: TeachingTip pre-load (Feature 005 — returns empty)
        # 3rd: AthleteMotionAnalysis lookup (returns empty list)
        task_result = MagicMock()
        task_result.scalar_one_or_none.return_value = task

        tips_result = MagicMock()
        tips_result.scalars.return_value.all.return_value = []

        analyses_result = MagicMock()
        analyses_result.scalars.return_value.all.return_value = []

        override_db.execute = AsyncMock(
            side_effect=[task_result, tips_result, analyses_result]
        )

        resp = await client.get(f"/api/v1/tasks/{TASK_ID}/result")
        assert resp.status_code == 200


# ── DELETE /api/v1/tasks/{task_id} ───────────────────────────────────────────

class TestDeleteTask:
    @pytest.mark.asyncio
    async def test_invalid_uuid_returns_404(self, client, override_db):
        resp = await client.delete("/api/v1/tasks/bad-uuid")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_task_not_found_returns_404(self, client, override_db):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        override_db.execute = AsyncMock(return_value=result)

        resp = await client.delete(f"/api/v1/tasks/{TASK_ID}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types")
    async def test_soft_delete_sets_deleted_at(self, client, override_db):
        task = make_task(status="success")
        task.deleted_at = None  # mutable
        result = MagicMock()
        result.scalar_one_or_none.return_value = task
        override_db.execute = AsyncMock(return_value=result)

        resp = await client.delete(f"/api/v1/tasks/{TASK_ID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == str(TASK_ID)
        assert "deleted_at" in body
        assert "24 小时" in body["message"]
        # Verify deleted_at was set on the task object
        assert task.deleted_at is not None
        override_db.commit.assert_awaited_once()
