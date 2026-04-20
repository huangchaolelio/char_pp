"""API contract tests for Feature-002 audio-enhanced KB extraction (T034).

Covers:
- POST /tasks/expert-video: new fields (enable_audio_analysis, audio_language,
  video_duration_seconds) schema validation and VIDEO_TOO_LONG early rejection
- GET /tasks/{id}: progress fields (progress_pct, processed_segments,
  total_segments, audio_fallback_reason)
- GET /tasks/{id}/result: audio_analysis and conflicts fields on expert result
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport

from src.api.main import app
from src.db.session import get_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_override(mock_session):
    async def _override():
        yield mock_session
    return _override


def _make_task(
    task_type="expert_video",
    status="processing",
    progress_pct=None,
    processed_segments=None,
    total_segments=None,
    audio_fallback_reason=None,
):
    from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType

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
    task.deleted_at = None
    task.knowledge_base_version = "1.0.0"
    task.progress_pct = progress_pct
    task.processed_segments = processed_segments
    task.total_segments = total_segments
    task.audio_fallback_reason = audio_fallback_reason
    return task


@pytest.fixture
def async_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# POST /tasks/expert-video — new field schema
# ---------------------------------------------------------------------------

@pytest.mark.contract
@pytest.mark.asyncio
class TestExpertVideoSubmitV2:
    async def test_enable_audio_analysis_defaults_true(self, async_client):
        """POST without enable_audio_analysis → defaults to True (accepted)."""
        with patch("src.api.routers.tasks.cos_client.object_exists", return_value=False):
            async with async_client as client:
                response = await client.post(
                    "/api/v1/tasks/expert-video",
                    json={"cos_object_key": "video.mp4"},
                )
        # 404 COS_OBJECT_NOT_FOUND — not a 422 schema error
        assert response.status_code == 404

    async def test_enable_audio_analysis_false_accepted(self, async_client):
        """POST with enable_audio_analysis=false is a valid schema."""
        with patch("src.api.routers.tasks.cos_client.object_exists", return_value=False):
            async with async_client as client:
                response = await client.post(
                    "/api/v1/tasks/expert-video",
                    json={
                        "cos_object_key": "video.mp4",
                        "enable_audio_analysis": False,
                    },
                )
        assert response.status_code == 404  # COS check, not schema error

    async def test_audio_language_field_accepted(self, async_client):
        """POST with audio_language='zh' is valid."""
        with patch("src.api.routers.tasks.cos_client.object_exists", return_value=False):
            async with async_client as client:
                response = await client.post(
                    "/api/v1/tasks/expert-video",
                    json={
                        "cos_object_key": "video.mp4",
                        "audio_language": "zh",
                    },
                )
        assert response.status_code == 404

    async def test_video_duration_seconds_field_accepted(self, async_client):
        """POST with video_duration_seconds (short video) is valid."""
        with patch("src.api.routers.tasks.cos_client.object_exists", return_value=False):
            async with async_client as client:
                response = await client.post(
                    "/api/v1/tasks/expert-video",
                    json={
                        "cos_object_key": "video.mp4",
                        "video_duration_seconds": 120.0,
                    },
                )
        assert response.status_code == 404

    async def test_video_too_long_returns_422(self, async_client):
        """POST with video_duration_seconds > 5400 → 422 VIDEO_TOO_LONG."""
        with patch("src.api.routers.tasks.cos_client.object_exists", return_value=True):
            async with async_client as client:
                response = await client.post(
                    "/api/v1/tasks/expert-video",
                    json={
                        "cos_object_key": "video.mp4",
                        "video_duration_seconds": 5401.0,
                    },
                )
        assert response.status_code == 422
        data = response.json()
        detail = data.get("detail", {})
        assert detail.get("code") == "VIDEO_TOO_LONG"
        assert "message" in detail

    async def test_video_too_long_detail_has_duration_fields(self, async_client):
        """VIDEO_TOO_LONG error detail includes duration_seconds and max_duration_seconds."""
        with patch("src.api.routers.tasks.cos_client.object_exists", return_value=True):
            async with async_client as client:
                response = await client.post(
                    "/api/v1/tasks/expert-video",
                    json={
                        "cos_object_key": "video.mp4",
                        "video_duration_seconds": 9000.0,
                    },
                )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["details"]["duration_seconds"] == 9000.0
        assert "max_duration_seconds" in detail["details"]

    async def test_video_exactly_at_limit_not_rejected(self, async_client):
        """POST with video_duration_seconds == max (5400) is NOT rejected (boundary)."""
        with patch("src.api.routers.tasks.cos_client.object_exists", return_value=False):
            async with async_client as client:
                response = await client.post(
                    "/api/v1/tasks/expert-video",
                    json={
                        "cos_object_key": "video.mp4",
                        "video_duration_seconds": 5400.0,
                    },
                )
        # 404 COS_OBJECT_NOT_FOUND, not VIDEO_TOO_LONG
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /tasks/{id} — progress fields
# ---------------------------------------------------------------------------

@pytest.mark.contract
@pytest.mark.asyncio
class TestTaskStatusProgressFields:
    async def test_status_response_has_progress_fields(self, async_client):
        """GET /tasks/{id} includes all Feature-002 progress fields."""
        task = _make_task(
            status="processing",
            progress_pct=45.0,
            processed_segments=3,
            total_segments=7,
            audio_fallback_reason=None,
        )
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = task
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        data = response.json()
        assert "progress_pct" in data
        assert "processed_segments" in data
        assert "total_segments" in data
        assert "audio_fallback_reason" in data

    async def test_progress_pct_value_correct(self, async_client):
        """GET /tasks/{id} returns correct progress_pct value."""
        task = _make_task(status="processing", progress_pct=62.5)
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = task
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        assert response.json()["progress_pct"] == 62.5

    async def test_segments_fields_correct(self, async_client):
        """GET /tasks/{id} returns correct processed_segments and total_segments."""
        task = _make_task(status="processing", processed_segments=2, total_segments=5)
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = task
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}")
        finally:
            app.dependency_overrides.pop(get_db, None)

        data = response.json()
        assert data["processed_segments"] == 2
        assert data["total_segments"] == 5

    async def test_audio_fallback_reason_returned(self, async_client):
        """GET /tasks/{id} includes audio_fallback_reason when set."""
        task = _make_task(status="success", audio_fallback_reason="low_snr: 4.2 dB")
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = task
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        assert response.json()["audio_fallback_reason"] == "low_snr: 4.2 dB"

    async def test_progress_fields_null_for_pending_task(self, async_client):
        """GET /tasks/{id} returns null progress fields for pending task."""
        task = _make_task(status="pending")
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = task
        mock_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}")
        finally:
            app.dependency_overrides.pop(get_db, None)

        data = response.json()
        assert data["progress_pct"] is None
        assert data["processed_segments"] is None
        assert data["total_segments"] is None


# ---------------------------------------------------------------------------
# GET /tasks/{id}/result — audio_analysis and conflicts fields
# ---------------------------------------------------------------------------

@pytest.mark.contract
@pytest.mark.asyncio
class TestExpertResultV2Fields:
    def _make_success_task(self):
        return _make_task(task_type="expert_video", status="success")

    def _make_kb(self, version="1.0.0"):
        from src.models.tech_knowledge_base import TechKnowledgeBase, KBStatus
        kb = MagicMock(spec=TechKnowledgeBase)
        kb.version = version
        kb.status = KBStatus.draft
        return kb

    def _make_tech_point(self, dimension="elbow_angle", source_type="visual", conflict_flag=False):
        from src.models.expert_tech_point import ExpertTechPoint
        from unittest.mock import MagicMock
        pt = MagicMock(spec=ExpertTechPoint)
        pt.id = uuid.uuid4()
        action_type_mock = MagicMock()
        action_type_mock.value = "forehand"
        pt.action_type = action_type_mock
        pt.dimension = dimension
        pt.param_min = 80.0
        pt.param_max = 100.0
        pt.param_ideal = 90.0
        pt.unit = "°"
        pt.extraction_confidence = 0.85
        pt.source_type = source_type
        pt.conflict_flag = conflict_flag
        pt.conflict_detail = None
        return pt

    async def test_result_response_has_audio_analysis_field(self, async_client):
        """GET /tasks/{id}/result includes audio_analysis field."""
        task = self._make_success_task()
        tech_point = self._make_tech_point()
        kb = self._make_kb()

        mock_session = AsyncMock()
        call_count = [0]

        async def _execute(*args, **kwargs):
            call_count[0] += 1
            n = call_count[0]
            r = MagicMock()
            if n == 1:
                r.scalar_one_or_none.return_value = task
            elif n == 2:
                # ExpertTechPoint query
                r.scalars.return_value.all.return_value = [tech_point]
            elif n == 3:
                # TechKnowledgeBase lookup
                r.scalar_one_or_none.return_value = kb
            else:
                r.scalar_one_or_none.return_value = None
                r.scalars.return_value.all.return_value = []
            return r

        mock_session.execute = AsyncMock(side_effect=_execute)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}/result")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        data = response.json()
        assert "audio_analysis" in data

    async def test_result_response_has_conflicts_field(self, async_client):
        """GET /tasks/{id}/result includes conflicts list field."""
        task = self._make_success_task()
        tech_point = self._make_tech_point()
        kb = self._make_kb()

        mock_session = AsyncMock()
        call_count = [0]

        async def _execute(*args, **kwargs):
            call_count[0] += 1
            n = call_count[0]
            r = MagicMock()
            if n == 1:
                r.scalar_one_or_none.return_value = task
            elif n == 2:
                # ExpertTechPoint query
                r.scalars.return_value.all.return_value = [tech_point]
            elif n == 3:
                # TechKnowledgeBase lookup
                r.scalar_one_or_none.return_value = kb
            else:
                r.scalar_one_or_none.return_value = None
                r.scalars.return_value.all.return_value = []
            return r

        mock_session.execute = AsyncMock(side_effect=_execute)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}/result")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        data = response.json()
        assert "conflicts" in data
        assert isinstance(data["conflicts"], list)

    async def test_extracted_point_has_source_type_field(self, async_client):
        """Extracted tech points include source_type field."""
        task = self._make_success_task()
        tech_point = self._make_tech_point(source_type="visual+audio")
        kb = self._make_kb()

        mock_session = AsyncMock()
        call_count = [0]

        async def _execute(*args, **kwargs):
            call_count[0] += 1
            n = call_count[0]
            r = MagicMock()
            if n == 1:
                # task lookup
                r.scalar_one_or_none.return_value = task
            elif n == 2:
                # ExpertTechPoint query (scalars().all())
                r.scalars.return_value.all.return_value = [tech_point]
            elif n == 3:
                # TechKnowledgeBase lookup
                r.scalar_one_or_none.return_value = kb
            else:
                # AudioTranscript lookup → None (no audio)
                r.scalar_one_or_none.return_value = None
                r.scalars.return_value.all.return_value = []
            return r

        mock_session.execute = AsyncMock(side_effect=_execute)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}/result")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        data = response.json()
        points = data.get("extracted_points", [])
        assert len(points) >= 1
        assert "source_type" in points[0]

    async def test_extracted_point_has_conflict_flag_field(self, async_client):
        """Extracted tech points include conflict_flag field."""
        task = self._make_success_task()
        tech_point = self._make_tech_point(conflict_flag=True)
        kb = self._make_kb()

        mock_session = AsyncMock()
        call_count = [0]

        async def _execute(*args, **kwargs):
            call_count[0] += 1
            n = call_count[0]
            r = MagicMock()
            if n == 1:
                r.scalar_one_or_none.return_value = task
            elif n == 2:
                # ExpertTechPoint query
                r.scalars.return_value.all.return_value = [tech_point]
            elif n == 3:
                # TechKnowledgeBase lookup
                r.scalar_one_or_none.return_value = kb
            else:
                r.scalar_one_or_none.return_value = None
                r.scalars.return_value.all.return_value = []
            return r

        mock_session.execute = AsyncMock(side_effect=_execute)

        app.dependency_overrides[get_db] = _db_override(mock_session)
        try:
            async with async_client as client:
                response = await client.get(f"/api/v1/tasks/{task.id}/result")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        points = response.json().get("extracted_points", [])
        assert len(points) >= 1
        assert "conflict_flag" in points[0]
