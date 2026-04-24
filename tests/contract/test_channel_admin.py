"""Contract tests for channel admin PATCH endpoint (Feature 013 US5 T050).

Covers ``PATCH /api/v1/admin/channels/{task_type}``:
  * Happy path with valid ``X-Admin-Token`` → 200 with updated snapshot.
  * Missing/invalid token → 403 ``ADMIN_TOKEN_INVALID``.
  * Unknown ``task_type`` → 400 ``INVALID_INPUT``.
  * Validation: ``queue_capacity`` / ``concurrency`` must be >0 (422).
  * ``enabled: false`` toggle is accepted.
  * Response carries ``X-Admin-Operation: true``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.models.analysis_task import TaskType
from src.services.task_channel_service import (
    ChannelConfigSnapshot,
    ChannelLiveSnapshot,
)


pytestmark = pytest.mark.contract


ADMIN_TOKEN = "test-admin-token-t050"


def _live(task_type: TaskType, *, cap: int, enabled: bool = True) -> ChannelLiveSnapshot:
    return ChannelLiveSnapshot(
        task_type=task_type,
        queue_capacity=cap,
        concurrency=2,
        current_pending=0,
        current_processing=0,
        remaining_slots=cap,
        enabled=enabled,
        recent_completion_rate_per_min=0.0,
    )


def _cfg(task_type: TaskType, *, cap: int, enabled: bool = True) -> ChannelConfigSnapshot:
    return ChannelConfigSnapshot(
        task_type=task_type,
        queue_capacity=cap,
        concurrency=2,
        enabled=enabled,
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
def client_with_token(db_no_op):
    from src.config import get_settings
    settings = get_settings()
    original = settings.admin_reset_token
    settings.admin_reset_token = ADMIN_TOKEN
    try:
        yield TestClient(app)
    finally:
        settings.admin_reset_token = original


class TestChannelAdminContract:
    def test_patch_capacity_happy_path(self, client_with_token):
        with patch(
            "src.api.routers.admin.TaskChannelService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.update_config = AsyncMock(
                return_value=_cfg(TaskType.kb_extraction, cap=80)
            )
            inst.get_snapshot = AsyncMock(
                return_value=_live(TaskType.kb_extraction, cap=80)
            )
            response = client_with_token.patch(
                "/api/v1/admin/channels/kb_extraction",
                json={"queue_capacity": 80},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["task_type"] == "kb_extraction"
        assert body["queue_capacity"] == 80
        assert response.headers.get("X-Admin-Operation") == "true"
        inst.update_config.assert_called_once()
        kwargs = inst.update_config.call_args.kwargs
        assert kwargs["queue_capacity"] == 80
        assert kwargs["concurrency"] is None
        assert kwargs["enabled"] is None

    def test_patch_disable_channel(self, client_with_token):
        with patch(
            "src.api.routers.admin.TaskChannelService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.update_config = AsyncMock(
                return_value=_cfg(
                    TaskType.video_classification, cap=5, enabled=False
                )
            )
            inst.get_snapshot = AsyncMock(
                return_value=_live(
                    TaskType.video_classification, cap=5, enabled=False
                )
            )
            response = client_with_token.patch(
                "/api/v1/admin/channels/video_classification",
                json={"enabled": False},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

        assert response.status_code == 200
        assert response.json()["enabled"] is False

    def test_missing_token_returns_403(self, client_with_token):
        response = client_with_token.patch(
            "/api/v1/admin/channels/kb_extraction",
            json={"queue_capacity": 10},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["error"]["code"] == "ADMIN_TOKEN_INVALID"

    def test_wrong_token_returns_403(self, client_with_token):
        response = client_with_token.patch(
            "/api/v1/admin/channels/kb_extraction",
            json={"queue_capacity": 10},
            headers={"X-Admin-Token": "wrong-token"},
        )
        assert response.status_code == 403

    def test_invalid_task_type_returns_400(self, client_with_token):
        response = client_with_token.patch(
            "/api/v1/admin/channels/made_up_type",
            json={"queue_capacity": 10},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"]["code"] == "INVALID_INPUT"

    def test_zero_capacity_rejected_422(self, client_with_token):
        response = client_with_token.patch(
            "/api/v1/admin/channels/kb_extraction",
            json={"queue_capacity": 0},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert response.status_code == 422

    def test_negative_concurrency_rejected_422(self, client_with_token):
        response = client_with_token.patch(
            "/api/v1/admin/channels/kb_extraction",
            json={"concurrency": -1},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert response.status_code == 422

    def test_extra_field_rejected_422(self, client_with_token):
        response = client_with_token.patch(
            "/api/v1/admin/channels/kb_extraction",
            json={"queue_capacity": 10, "mystery_field": True},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert response.status_code == 422

    def test_service_value_error_returns_400(self, client_with_token):
        with patch(
            "src.api.routers.admin.TaskChannelService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.update_config = AsyncMock(
                side_effect=ValueError(
                    "no task_channel_configs row for kb_extraction"
                )
            )
            response = client_with_token.patch(
                "/api/v1/admin/channels/kb_extraction",
                json={"queue_capacity": 10},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
        assert response.status_code == 400
        assert response.json()["detail"]["error"]["code"] == "INVALID_INPUT"
