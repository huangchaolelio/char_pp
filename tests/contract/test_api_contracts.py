"""API contract tests (T052).

Verifies all 8 endpoints return correct request/response structures,
error codes, and HTTP status codes.

Uses httpx.AsyncClient against the FastAPI app directly (no network required).
"""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport

from src.api.main import app
from src.db.session import get_db


def _make_task(task_type="expert_video", status="success", deleted_at=None, kb_version="1.0.0"):
    from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
    from datetime import datetime, timezone

    task = MagicMock(spec=AnalysisTask)
    task.id = uuid.uuid4()
    task.task_type = TaskType(task_type)
    task.status = TaskStatus(status)
    task.created_at = datetime.now(timezone.utc)
    task.started_at = None
    task.completed_at = None
    task.video_duration_seconds = None
    task.video_fps = None
    task.video_resolution = None
    task.deleted_at = deleted_at
    task.knowledge_base_version = kb_version
    return task


def _db_override(mock_session):
    """Return a FastAPI dependency override that yields mock_session."""
    async def _override():
        yield mock_session
    return _override


@pytest.fixture
def async_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.contract
@pytest.mark.asyncio
class TestTaskStatusAndDeleteEndpoints:
    """Feature-017: POST /tasks/expert-video 已下线，类名由 TestExpertVideoEndpoints
    重命名为 TestTaskStatusAndDeleteEndpoints，范围收束为 GET/DELETE /tasks/{id}."""

    async def test_get_task_status_not_found(self, async_client):
        """GET /tasks/{task_id} with unknown UUID → 404."""
        task_id = str(uuid.uuid4())
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task_id}")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 404
        data = response.json()
        assert data.get("detail", {}).get("code") == "TASK_NOT_FOUND"

    async def test_get_task_status_invalid_uuid(self, async_client):
        """GET /tasks/{task_id} with invalid UUID → 404."""
        async with async_client as client:
            response = await client.get("/api/v1/tasks/not-a-uuid")
        assert response.status_code == 404

    @pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types (Alembic 0012 removed these enum values)")
    async def test_get_task_result_not_ready(self, async_client):
        """GET /tasks/{task_id}/result when status != success → 409."""
        task = _make_task(status="processing")
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = task
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}/result")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 409
        data = response.json()
        assert data.get("detail", {}).get("code") == "TASK_NOT_READY"

    async def test_delete_task_not_found(self, async_client):
        """DELETE /tasks/{task_id} with unknown UUID → 404."""
        task_id = str(uuid.uuid4())
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.delete(f"/api/v1/tasks/{task_id}")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 404


@pytest.mark.contract
@pytest.mark.asyncio
class TestKnowledgeBaseEndpoints:
    async def test_get_kb_versions_empty(self, async_client):
        """GET /knowledge-base/versions → 200 with empty list."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get("/api/v1/knowledge-base/versions")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        data = response.json()
        assert "data" in data or isinstance(data, (list, dict))

    async def test_get_kb_version_not_found(self, async_client):
        """GET /knowledge-base/{version} with non-existent version → 404."""
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get("/api/v1/knowledge-base/9.9.9")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 404

    async def test_approve_kb_version_not_found(self, async_client):
        """POST /knowledge-base/{version}/approve with non-existent version → 404."""
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=MagicMock())

        mock_begin_cm = MagicMock()
        mock_begin_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_begin_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin_cm)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.post(
                    "/api/v1/knowledge-base/9.9.9/approve",
                    json={"approved_by": "张教练"},
                )
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 404


@pytest.mark.contract
@pytest.mark.asyncio
class TestErrorResponseFormat:
    async def test_error_response_has_code_and_message(self, async_client):
        """All error responses should have {detail: {code, message}} format."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{uuid.uuid4()}")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 404
        data = response.json()
        detail = data.get("detail", {})
        assert "code" in detail
        assert "message" in detail


@pytest.mark.contract
class TestFeature006TaskCoachContracts:
    """T024: ExpertVideoRequest schema 兼容 coach_id; GET /tasks/{id} returns coach_id.

    Feature-017: POST /tasks/expert-video 端点已下线，但 schema 本身仍保留作为
    /tasks/kb-extraction 等新接口的请求模型基类，此处仍测 schema 层兼容性。"""

    def test_expert_video_request_accepts_coach_id(self):
        from src.api.schemas.task import ExpertVideoRequest
        import uuid
        body = ExpertVideoRequest(cos_object_key="coach-videos/test.mp4", coach_id=uuid.uuid4())
        assert body.coach_id is not None

    def test_expert_video_request_coach_id_optional(self):
        from src.api.schemas.task import ExpertVideoRequest
        body = ExpertVideoRequest(cos_object_key="coach-videos/test.mp4")
        assert body.coach_id is None

    def test_task_status_response_has_coach_fields(self):
        from src.api.schemas.task import TaskStatusResponse
        import uuid
        from datetime import datetime, timezone
        resp = TaskStatusResponse(
            task_id=uuid.uuid4(), task_type="expert_video",
            status="pending", created_at=datetime.now(timezone.utc),
            coach_id=uuid.uuid4(), coach_name="张教练",
        )
        assert resp.coach_name == "张教练"

    def test_task_status_response_coach_fields_optional(self):
        from src.api.schemas.task import TaskStatusResponse
        import uuid
        from datetime import datetime, timezone
        resp = TaskStatusResponse(
            task_id=uuid.uuid4(), task_type="expert_video",
            status="pending", created_at=datetime.now(timezone.utc),
        )
        assert resp.coach_id is None
        assert resp.coach_name is None
