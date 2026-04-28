"""Contract tests for POST /api/v1/tasks/kb-extraction (Feature 013 US1).

Focus: CLASSIFICATION_REQUIRED pre-check + happy path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from src.utils.time_utils import now_cst
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


def _ok_result():
    from src.models.analysis_task import TaskType
    from src.services.task_channel_service import ChannelLiveSnapshot
    from src.services.task_submission_service import (
        SubmissionBatchResult,
        SubmissionOutcome,
    )

    snap = ChannelLiveSnapshot(
        task_type=TaskType.kb_extraction,
        queue_capacity=50, concurrency=2,
        current_pending=1, current_processing=0,
        remaining_slots=49, enabled=True,
        recent_completion_rate_per_min=0.0,
    )
    return SubmissionBatchResult(
        task_type=TaskType.kb_extraction,
        accepted=1, rejected=0,
        items=[SubmissionOutcome(
            index=0, accepted=True, task_id=uuid4(),
            cos_object_key="videos/coach_a/forehand_loop.mp4",
        )],
        channel=snap,
        submitted_at=now_cst(),
    )


@pytest.fixture
def db_no_op():
    from src.db.session import get_db

    async def _fake_db():  # pragma: no cover
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(db_no_op):
    return TestClient(app)


@pytest.mark.contract
class TestKbExtractionSubmitContract:
    def test_happy_path_200(self, client):
        with (
            patch("src.api.routers.tasks._F13ClassificationGateService") as GateCls,
            patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls,
        ):
            GateCls.return_value.check_classified = AsyncMock(return_value=True)
            GateCls.return_value.get_tech_category = AsyncMock(return_value="forehand_loop_fast")
            SvcCls.return_value.submit_batch = AsyncMock(return_value=_ok_result())

            response = client.post(
                "/api/v1/tasks/kb-extraction",
                json={
                    "cos_object_key": "videos/coach_a/forehand_loop.mp4",
                    "enable_audio_analysis": True,
                    "audio_language": "zh",
                },
            )
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["task_type"] == "kb_extraction"
        assert body["accepted"] == 1
        assert body["items"][0]["accepted"] is True

    def test_unclassified_returns_400_classification_required(self, client):
        with patch("src.api.routers.tasks._F13ClassificationGateService") as GateCls:
            GateCls.return_value.check_classified = AsyncMock(return_value=False)
            GateCls.return_value.get_tech_category = AsyncMock(return_value=None)

            response = client.post(
                "/api/v1/tasks/kb-extraction",
                json={"cos_object_key": "videos/coach_a/new_clip.mp4"},
            )
        assert response.status_code == 400
        body = response.json()
        # Feature-017：错误信封
        assert body["success"] is False
        err = body["error"]
        assert err["code"] == "CLASSIFICATION_REQUIRED"
        assert err["details"]["cos_object_key"] == "videos/coach_a/new_clip.mp4"
        assert err["details"]["current_tech_category"] is None

    def test_row_exists_but_unclassified_returns_400(self, client):
        """Row in coach_video_classifications has tech_category='unclassified'."""
        with patch("src.api.routers.tasks._F13ClassificationGateService") as GateCls:
            GateCls.return_value.check_classified = AsyncMock(return_value=False)
            GateCls.return_value.get_tech_category = AsyncMock(return_value="unclassified")

            response = client.post(
                "/api/v1/tasks/kb-extraction",
                json={"cos_object_key": "videos/coach_a/other.mp4"},
            )
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "CLASSIFICATION_REQUIRED"

    def test_missing_cos_object_key_returns_422(self, client):
        response = client.post("/api/v1/tasks/kb-extraction", json={})
        assert response.status_code == 422

    def test_default_audio_flags(self, client):
        """enable_audio_analysis & audio_language have schema defaults."""
        with (
            patch("src.api.routers.tasks._F13ClassificationGateService") as GateCls,
            patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls,
        ):
            GateCls.return_value.check_classified = AsyncMock(return_value=True)
            GateCls.return_value.get_tech_category = AsyncMock(return_value="serve")
            SvcCls.return_value.submit_batch = AsyncMock(return_value=_ok_result())
            response = client.post(
                "/api/v1/tasks/kb-extraction",
                json={"cos_object_key": "videos/coach_b/serve_01.mp4"},
            )
        assert response.status_code == 200
