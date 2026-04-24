"""Contract tests for channel status endpoints (Feature 013 US5 T049).

Covers:
  * ``GET /api/v1/task-channels`` — returns ``{"channels": [3 snapshots]}``.
  * ``GET /api/v1/task-channels/{task_type}`` — single channel snapshot;
    404 for unknown ``task_type``.

Mocks ``TaskChannelService`` so these run without a DB; asserts the wire
shape matches ``contracts/channel_status.yaml``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.models.analysis_task import TaskType
from src.services.task_channel_service import ChannelLiveSnapshot


pytestmark = pytest.mark.contract


def _snap(task_type: TaskType, *, cap: int = 5, pending: int = 0) -> ChannelLiveSnapshot:
    return ChannelLiveSnapshot(
        task_type=task_type,
        queue_capacity=cap,
        concurrency=1,
        current_pending=pending,
        current_processing=0,
        remaining_slots=max(0, cap - pending),
        enabled=True,
        recent_completion_rate_per_min=1.5,
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


class TestChannelStatusContract:
    def test_list_all_channels_returns_three(self, client):
        with patch(
            "src.api.routers.task_channels.TaskChannelService"
        ) as SvcCls:
            inst = SvcCls.return_value

            async def _side(_db, tt: TaskType):
                return _snap(tt, cap={"classification": 5,
                                      "kb_extraction": 50,
                                      "athlete_diagnosis": 20}.get(tt.value, 5))

            inst.get_snapshot = AsyncMock(side_effect=_side)

            response = client.get("/api/v1/task-channels")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "channels" in body
        assert len(body["channels"]) == 3
        types = {c["task_type"] for c in body["channels"]}
        assert types == {
            "video_classification", "kb_extraction", "athlete_diagnosis"
        }
        for ch in body["channels"]:
            assert set(ch.keys()) >= {
                "task_type", "queue_capacity", "concurrency",
                "current_pending", "current_processing", "remaining_slots",
                "enabled", "recent_completion_rate_per_min",
            }

    def test_single_channel_happy_path(self, client):
        with patch(
            "src.api.routers.task_channels.TaskChannelService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.get_snapshot = AsyncMock(
                return_value=_snap(TaskType.kb_extraction, cap=50, pending=3)
            )
            response = client.get("/api/v1/task-channels/kb_extraction")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["task_type"] == "kb_extraction"
        assert body["queue_capacity"] == 50
        assert body["current_pending"] == 3
        assert body["remaining_slots"] == 47
        assert body["enabled"] is True

    def test_unknown_task_type_returns_404(self, client):
        response = client.get("/api/v1/task-channels/unknown_type")
        assert response.status_code == 404
        assert response.json()["detail"]["error"]["code"] == "TASK_TYPE_NOT_FOUND"

    def test_classification_channel(self, client):
        with patch(
            "src.api.routers.task_channels.TaskChannelService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.get_snapshot = AsyncMock(
                return_value=_snap(TaskType.video_classification, cap=5)
            )
            response = client.get("/api/v1/task-channels/video_classification")
        assert response.status_code == 200
        assert response.json()["task_type"] == "video_classification"

    def test_diagnosis_channel(self, client):
        with patch(
            "src.api.routers.task_channels.TaskChannelService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.get_snapshot = AsyncMock(
                return_value=_snap(TaskType.athlete_diagnosis, cap=20)
            )
            response = client.get("/api/v1/task-channels/athlete_diagnosis")
        assert response.status_code == 200
        assert response.json()["task_type"] == "athlete_diagnosis"
        assert response.json()["queue_capacity"] == 20
