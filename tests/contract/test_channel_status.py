"""Contract tests for channel status endpoints (Feature 013 US5 T049).

Feature-017 已全量切换到 ``SuccessEnvelope`` / ``ErrorEnvelope``：
  * ``GET /api/v1/task-channels`` — returns ``{success, data=[...snapshots], meta}``.
  * ``GET /api/v1/task-channels/{task_type}`` — single channel snapshot包在 data 字段中;
    400 + ``INVALID_ENUM_VALUE`` for unknown ``task_type``（架构 v1.4.0 修正：
    枚举非法值属于客户端错误，不是“资源不存在”，状态码从 404 改为 400）.

Mocks ``TaskChannelService`` so these run without a DB.
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
                                      "athlete_diagnosis": 20,
                                      "video_preprocessing": 20,
                                      # Feature-020 新增 2 个 task_type
                                      "athlete_video_classification": 10,
                                      "athlete_video_preprocessing": 20,
                                      }.get(tt.value, 5))

            inst.get_snapshot = AsyncMock(side_effect=_side)

            response = client.get("/api/v1/task-channels")
        assert response.status_code == 200, response.text
        body = response.json()
        # Feature-017：从返回 ``{"channels":[...]}`` 改为 SuccessEnvelope。
        assert body["success"] is True
        # Feature-017 阶段 5 T054：统一分页参数后 meta 恒非空（即使枚举型全量返回）
        assert body["meta"] is not None
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == 20
        # Feature-020 新增 2 个 task_type（athlete_video_classification /
        # athlete_video_preprocessing），端点枚举型全量返回 6 条。
        assert body["meta"]["total"] == 6
        channels = body["data"]
        assert len(channels) == 6
        types = {c["task_type"] for c in channels}
        assert types == {
            "video_classification", "kb_extraction",
            "athlete_diagnosis", "video_preprocessing",
            "athlete_video_classification", "athlete_video_preprocessing",
        }
        for ch in channels:
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
        assert body["success"] is True
        data = body["data"]
        assert data["task_type"] == "kb_extraction"
        assert data["queue_capacity"] == 50
        assert data["current_pending"] == 3
        assert data["remaining_slots"] == 47
        assert data["enabled"] is True

    def test_unknown_task_type_returns_400(self, client):
        """Feature-017：枚举非法值 → 400 + INVALID_ENUM_VALUE（而非旧的 404 + TASK_TYPE_NOT_FOUND）."""
        response = client.get("/api/v1/task-channels/unknown_type")
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_ENUM_VALUE"
        assert body["error"]["details"]["field"] == "task_type"
        assert body["error"]["details"]["value"] == "unknown_type"
        assert "video_classification" in body["error"]["details"]["allowed"]

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
        body = response.json()
        assert body["success"] is True
        assert body["data"]["task_type"] == "video_classification"

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
        body = response.json()
        assert body["success"] is True
        assert body["data"]["task_type"] == "athlete_diagnosis"
        assert body["data"]["queue_capacity"] == 20
