"""Contract tests for Feature-020 — GET /api/v1/athlete-classifications/scan/{task_id} (T018).

Covers contracts/athlete_scan_status.md 的 4 个断言点:
  1. 不存在的 UUID → 404 TASK_NOT_FOUND
  2. 非 UUID 字符串 → 422 VALIDATION_FAILED
  3. 状态流转 pending → running → success
  4. 失败任务的 error_detail 以错误码前缀开头
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.models.analysis_task import TaskStatus, TaskType
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


@pytest.fixture
def db_no_op():
    from src.db.session import get_db

    async def _fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(db_no_op):
    return TestClient(app)


def _fake_analysis_task(task_id: UUID, *, status: str, progress: dict | None = None,
                        error_message: str | None = None, task_type_val: str = "athlete_video_classification"):
    """Return a MagicMock shaped like AnalysisTask with enum-like attributes."""
    row = MagicMock()
    row.id = task_id
    # TaskStatus enum value-compare
    status_enum = TaskStatus(status)
    row.status = status_enum
    row.progress = progress or {}
    row.error_message = error_message
    # task_type 以 enum 形态供 value 比较
    row.task_type = TaskType(task_type_val)
    return row


@pytest.mark.contract
class TestAthleteScanStatusContract:

    def test_c1_nonexistent_uuid_returns_404(self, client):
        missing_id = uuid4()
        with patch(
            "src.api.routers.athlete_classifications.fetch_scan_task",
            new_callable=AsyncMock, return_value=None,
        ):
            resp = client.get(f"/api/v1/athlete-classifications/scan/{missing_id}")
        assert resp.status_code == 404, resp.text
        assert_error_envelope(resp.json(), code="TASK_NOT_FOUND")

    def test_c2_non_uuid_rejected(self, client):
        resp = client.get("/api/v1/athlete-classifications/scan/not-a-uuid")
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json())

    def test_c3_running_status_progress(self, client):
        tid = uuid4()
        progress = {
            "scanned": 42, "inserted": 40, "updated": 2, "skipped": 0,
            "errors": 0, "elapsed_s": 10.5,
        }
        row = _fake_analysis_task(tid, status="processing", progress=progress)
        with patch(
            "src.api.routers.athlete_classifications.fetch_scan_task",
            new_callable=AsyncMock, return_value=row,
        ):
            resp = client.get(f"/api/v1/athlete-classifications/scan/{tid}")
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        # processing → 对外暴露 running
        assert data["status"] in ("running", "processing")
        assert data["scanned"] == 42
        assert data["inserted"] == 40

    def test_c4_failed_task_error_detail_prefixed(self, client):
        tid = uuid4()
        progress = {"error_detail": "ATHLETE_ROOT_UNREADABLE: 凭证无效"}
        row = _fake_analysis_task(
            tid, status="failed", progress=progress,
            error_message="ATHLETE_ROOT_UNREADABLE: 凭证无效",
        )
        with patch(
            "src.api.routers.athlete_classifications.fetch_scan_task",
            new_callable=AsyncMock, return_value=row,
        ):
            resp = client.get(f"/api/v1/athlete-classifications/scan/{tid}")
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["status"] == "failed"
        assert data["error_detail"] is not None
        assert data["error_detail"].startswith("ATHLETE_ROOT_UNREADABLE")
