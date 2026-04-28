"""Contract tests for POST /api/v1/admin/reset-task-pipeline (Feature 013 US4).

Mocks TaskResetService so the test runs without touching PostgreSQL, asserts
the HTTP contract defined in
``specs/013-task-pipeline-redesign/contracts/data_reset.yaml``:

  * Happy path → 200 with ``reset_at`` / ``deleted_counts`` / ``preserved_counts``
    / ``duration_ms`` / ``dry_run``（信封化后业务字段全部位于 ``body["data"]``）.
  * Wrong / missing token → **401** ``ADMIN_TOKEN_INVALID``（Feature-017：
    从 403 对齐为 401 未认证语义）.
  * Empty request body (missing confirmation_token) → 422.
  * Dry-run flag is forwarded to the service.
  * Response carries ``X-Admin-Operation: true`` header for audit.
  * Server with unconfigured ``ADMIN_RESET_TOKEN`` → 500 ``ADMIN_TOKEN_NOT_CONFIGURED``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from src.utils.time_utils import now_cst
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.services.task_reset_service import ResetReportData


pytestmark = pytest.mark.contract


ADMIN_TOKEN = "test-admin-token-t043"


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
    """TestClient with ADMIN_RESET_TOKEN set via settings cache override."""
    from src.config import get_settings

    settings = get_settings()
    original = settings.admin_reset_token
    settings.admin_reset_token = ADMIN_TOKEN
    try:
        yield TestClient(app)
    finally:
        settings.admin_reset_token = original


@pytest.fixture
def client_without_token(db_no_op):
    from src.config import get_settings

    settings = get_settings()
    original = settings.admin_reset_token
    settings.admin_reset_token = ""
    try:
        yield TestClient(app)
    finally:
        settings.admin_reset_token = original


def _make_report(dry_run: bool) -> ResetReportData:
    return ResetReportData(
        reset_at=now_cst(),
        dry_run=dry_run,
        deleted_counts={
            "analysis_tasks": 2589,
            "audio_transcripts": 120,
            "expert_tech_points": 3400,
            "tech_knowledge_bases_draft": 5,
        },
        preserved_counts={
            "coaches": 20,
            "coach_video_classifications": 1015,
            "tech_knowledge_bases_published": 3,
        },
        duration_ms=42,
    )


class TestDataResetContract:
    def test_happy_path_returns_200_with_report(self, client_with_token):
        with patch(
            "src.api.routers.admin.TaskResetService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.reset = AsyncMock(return_value=_make_report(dry_run=False))

            response = client_with_token.post(
                "/api/v1/admin/reset-task-pipeline",
                json={"confirmation_token": ADMIN_TOKEN},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert set(data.keys()) >= {
            "reset_at", "deleted_counts", "preserved_counts",
            "duration_ms", "dry_run",
        }
        assert data["dry_run"] is False
        assert data["deleted_counts"]["analysis_tasks"] == 2589
        assert data["preserved_counts"]["coaches"] == 20
        assert data["duration_ms"] >= 0
        assert response.headers.get("X-Admin-Operation") == "true"

    def test_dry_run_forwarded_to_service(self, client_with_token):
        with patch("src.api.routers.admin.TaskResetService") as SvcCls:
            inst = SvcCls.return_value
            inst.reset = AsyncMock(return_value=_make_report(dry_run=True))

            response = client_with_token.post(
                "/api/v1/admin/reset-task-pipeline",
                json={"confirmation_token": ADMIN_TOKEN, "dry_run": True},
            )

        assert response.status_code == 200
        assert response.json()["data"]["dry_run"] is True
        inst.reset.assert_called_once()
        kwargs = inst.reset.call_args.kwargs
        assert kwargs.get("dry_run") is True

    def test_wrong_token_returns_401(self, client_with_token):
        response = client_with_token.post(
            "/api/v1/admin/reset-task-pipeline",
            json={"confirmation_token": "obviously-wrong"},
        )
        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "ADMIN_TOKEN_INVALID"

    def test_empty_token_returns_422(self, client_with_token):
        response = client_with_token.post(
            "/api/v1/admin/reset-task-pipeline",
            json={"confirmation_token": ""},
        )
        # min_length=1 on the schema → Pydantic 422 before we hit the handler.
        assert response.status_code == 422

    def test_missing_body_returns_422(self, client_with_token):
        response = client_with_token.post(
            "/api/v1/admin/reset-task-pipeline", json={}
        )
        assert response.status_code == 422

    def test_unconfigured_server_returns_500(self, client_without_token):
        response = client_without_token.post(
            "/api/v1/admin/reset-task-pipeline",
            json={"confirmation_token": "anything"},
        )
        assert response.status_code == 500
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "ADMIN_TOKEN_NOT_CONFIGURED"

    def test_extra_field_rejected(self, client_with_token):
        response = client_with_token.post(
            "/api/v1/admin/reset-task-pipeline",
            json={"confirmation_token": ADMIN_TOKEN, "unknown": 1},
        )
        assert response.status_code == 422
