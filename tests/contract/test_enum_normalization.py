"""Contract tests — 枚举归一化（Feature-017 阶段 5 T056/T057）.

覆盖场景：
  1. **大写输入**：`?status=PROCESSING` 等价于 `?status=processing`（200）
  2. **中划线输入**：`?task_type=video-classification` 等价于 `video_classification`（200）
  3. **混合输入（大写 + 中划线 + 空白）**：`  Video-Classification  ` 正确归一化
  4. **非法值**：`?status=not_a_status` → 400 + `INVALID_ENUM_VALUE`，`details.allowed` 含合法取值

为避免对真实 DB 的依赖，使用 TestClient + dependency_overrides 注入空 mock。
归一化由 ``src/api/enums.py::normalize_enum_value`` + ``parse_enum_param`` /
``validate_enum_choice`` 三件套统一实现；本套测试锁定其外部语义。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.db.session import get_db


@pytest.fixture()
def client_with_empty_db():
    """TestClient with DB calls returning empty results (to focus on 枚举解析路径)."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar_one.return_value = 0
    mock_result.scalar.return_value = 0
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def _override():
        yield mock_session

    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── GET /tasks ?status=... 归一化 ─────────────────────────────────────────────

class TestListTasksStatusNormalization:
    """Feature-017 T056：status 查询参数归一化.

    ``TaskStatus`` 合法取值为 ``pending/processing/success/failed/rejected``。
    """

    def test_uppercase_status_accepted(self, client_with_empty_db: TestClient) -> None:
        resp = client_with_empty_db.get("/api/v1/tasks?status=PROCESSING")
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_mixedcase_with_whitespace_accepted(self, client_with_empty_db: TestClient) -> None:
        resp = client_with_empty_db.get("/api/v1/tasks?status=%20Processing%20")
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_invalid_status_rejected(self, client_with_empty_db: TestClient) -> None:
        resp = client_with_empty_db.get("/api/v1/tasks?status=not_a_status")
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_ENUM_VALUE"
        assert body["error"]["details"]["field"] == "status"
        assert "allowed" in body["error"]["details"]


# ── GET /tasks ?task_type=... 归一化 ──────────────────────────────────────────

class TestListTasksTaskTypeNormalization:
    """Feature-017 T056：task_type 归一化（中划线 → 下划线）."""

    def test_hyphen_task_type_accepted(self, client_with_empty_db: TestClient) -> None:
        resp = client_with_empty_db.get("/api/v1/tasks?task_type=video-classification")
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_uppercase_task_type_accepted(self, client_with_empty_db: TestClient) -> None:
        resp = client_with_empty_db.get("/api/v1/tasks?task_type=KB_EXTRACTION")
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_invalid_task_type_rejected(self, client_with_empty_db: TestClient) -> None:
        resp = client_with_empty_db.get("/api/v1/tasks?task_type=unknown_type")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "INVALID_ENUM_VALUE"


# ── GET /task-channels/{task_type} 路径参数归一化 ────────────────────────────

class TestTaskChannelsPathParamNormalization:
    """Feature-017 T056：task-channels 路径参数归一化（大写 / 中划线）."""

    def test_uppercase_path_param_accepted(self, client_with_empty_db: TestClient) -> None:
        # 路径参数含中划线的 URL 不规范（FastAPI 按原样匹配路由），只测大写
        from unittest.mock import patch

        with patch(
            "src.api.routers.task_channels.TaskChannelService"
        ) as SvcCls:
            inst = SvcCls.return_value

            async def _mock_snap(_db, tt):
                from src.services.task_channel_service import ChannelLiveSnapshot
                return ChannelLiveSnapshot(
                    task_type=tt,
                    queue_capacity=10,
                    concurrency=1,
                    current_pending=0,
                    current_processing=0,
                    remaining_slots=10,
                    enabled=True,
                    recent_completion_rate_per_min=0.0,
                )

            inst.get_snapshot = AsyncMock(side_effect=_mock_snap)

            resp = client_with_empty_db.get("/api/v1/task-channels/KB_EXTRACTION")
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True
        assert resp.json()["data"]["task_type"] == "kb_extraction"

    def test_invalid_path_param_rejected(self, client_with_empty_db: TestClient) -> None:
        resp = client_with_empty_db.get("/api/v1/task-channels/not_a_channel")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "INVALID_ENUM_VALUE"


# ── POST /classifications/scan 请求体枚举归一化 ──────────────────────────────

class TestScanModeBodyNormalization:
    """Feature-017 T056：scan_mode 请求体字段归一化."""

    def test_uppercase_scan_mode_accepted(self, client_with_empty_db: TestClient) -> None:
        """``scan_mode=FULL`` 应被 validate_enum_choice 归一化为 ``full`` 并通过校验.

        本测试关心归一化层行为（不应被 scan_mode 校验拒绝），因此 mock 整个
        scan_cos_videos task 对象，避免真实 Celery 连接。
        """
        from unittest.mock import patch

        mock_task = MagicMock()
        mock_result = MagicMock()
        mock_result.id = "mock-task-id"
        mock_task.apply_async.return_value = mock_result

        # classification_task 模块层的 task 对象本身，而非其 apply_async 属性
        with patch.dict(
            "sys.modules",
            {"src.workers.classification_task": MagicMock(scan_cos_videos=mock_task)},
        ):
            resp = client_with_empty_db.post(
                "/api/v1/classifications/scan",
                json={"scan_mode": "FULL"},
            )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["success"] is True

    def test_invalid_scan_mode_rejected(self, client_with_empty_db: TestClient) -> None:
        resp = client_with_empty_db.post(
            "/api/v1/classifications/scan",
            json={"scan_mode": "not_a_mode"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "INVALID_ENUM_VALUE"
        assert body["error"]["details"]["field"] == "scan_mode"
