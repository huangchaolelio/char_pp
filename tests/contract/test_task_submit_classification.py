"""Contract tests for POST /api/v1/tasks/classification (Feature 013 US1).

Mocks TaskSubmissionService + TaskChannelService so the test runs without
PostgreSQL or Celery, focusing strictly on the request/response contract
defined by specs/013-task-pipeline-redesign/contracts/task_submit.yaml.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


def _make_service_result(accepted: int = 1, rejected: int = 0):
    from src.models.analysis_task import TaskType
    from src.services.task_channel_service import ChannelLiveSnapshot
    from src.services.task_submission_service import (
        SubmissionBatchResult,
        SubmissionOutcome,
    )

    outcomes = []
    for i in range(accepted):
        outcomes.append(
            SubmissionOutcome(
                index=i,
                accepted=True,
                task_id=uuid4(),
                cos_object_key=f"video_{i}.mp4",
            )
        )
    for j in range(rejected):
        outcomes.append(
            SubmissionOutcome(
                index=accepted + j,
                accepted=False,
                cos_object_key=f"video_{accepted + j}.mp4",
                rejection_code="QUEUE_FULL",
                rejection_message="channel full",
            )
        )
    snap = ChannelLiveSnapshot(
        task_type=TaskType.video_classification,
        queue_capacity=5,
        concurrency=1,
        current_pending=accepted,
        current_processing=0,
        remaining_slots=max(0, 5 - accepted),
        enabled=True,
        recent_completion_rate_per_min=0.0,
    )
    return SubmissionBatchResult(
        task_type=TaskType.video_classification,
        accepted=accepted,
        rejected=rejected,
        items=outcomes,
        channel=snap,
        submitted_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def client(db_no_op):
    return TestClient(app)


@pytest.fixture
def db_no_op():
    """Override get_db dependency so FastAPI hands the handler a dummy session."""
    from src.db.session import get_db

    async def _fake_db():  # pragma: no cover — returns an AsyncMock session
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.contract
class TestClassificationSubmitContract:
    def test_single_submit_200_response_shape(self, client):
        """Happy path — all required fields present and typed."""
        with patch(
            "src.api.routers.tasks._F13TaskSubmissionService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.submit_batch = AsyncMock(return_value=_make_service_result(accepted=1))

            response = client.post(
                "/api/v1/tasks/classification",
                json={"cos_object_key": "videos/coach_a/shot_01.mp4"},
            )

        assert response.status_code == 200, response.text
        envelope = response.json()
        # Feature-017：业务载荷位于 data 字段
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["task_type"] == "video_classification"
        assert body["accepted"] == 1
        assert body["rejected"] == 0
        assert isinstance(body["items"], list) and len(body["items"]) == 1

        item = body["items"][0]
        assert set(item.keys()) >= {
            "index", "accepted", "task_id", "cos_object_key",
            "rejection_code", "rejection_message",
        }
        assert item["accepted"] is True

        ch = body["channel"]
        assert set(ch.keys()) >= {
            "task_type", "queue_capacity", "concurrency",
            "current_pending", "current_processing", "remaining_slots",
            "enabled", "recent_completion_rate_per_min",
        }
        assert ch["task_type"] == "video_classification"
        assert ch["queue_capacity"] == 5
        # submitted_at moved to envelope.data
        assert "submitted_at" in body

    def test_missing_cos_object_key_returns_422(self, client):
        response = client.post("/api/v1/tasks/classification", json={})
        assert response.status_code == 422

    def test_empty_cos_object_key_returns_422(self, client):
        response = client.post(
            "/api/v1/tasks/classification", json={"cos_object_key": ""}
        )
        assert response.status_code == 422

    def test_extra_field_rejected(self, client):
        """``extra='forbid'`` on the Pydantic schema."""
        response = client.post(
            "/api/v1/tasks/classification",
            json={"cos_object_key": "a.mp4", "unknown_field": True},
        )
        assert response.status_code == 422

    def test_channel_full_returns_200_with_rejection(self, client):
        """Capacity overflow is NOT a 4xx — it is a 200 with accepted=0, rejection_code set."""
        with patch(
            "src.api.routers.tasks._F13TaskSubmissionService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.submit_batch = AsyncMock(
                return_value=_make_service_result(accepted=0, rejected=1)
            )

            response = client.post(
                "/api/v1/tasks/classification",
                json={"cos_object_key": "videos/coach_a/shot_02.mp4"},
            )

        assert response.status_code == 200
        envelope = response.json()
        body = envelope["data"]
        assert body["accepted"] == 0
        assert body["rejected"] == 1
        assert body["items"][0]["rejection_code"] == "QUEUE_FULL"

    def test_channel_disabled_returns_503(self, client):
        """Feature-017：CHANNEL_DISABLED 按章程 v1.4.0 归类为 503（服务不可用）."""
        from src.services.task_submission_service import ChannelDisabledError

        with patch(
            "src.api.routers.tasks._F13TaskSubmissionService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.submit_batch = AsyncMock(
                side_effect=ChannelDisabledError("channel video_classification is disabled")
            )

            response = client.post(
                "/api/v1/tasks/classification",
                json={"cos_object_key": "v.mp4"},
            )

        assert response.status_code == 503
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "CHANNEL_DISABLED"
