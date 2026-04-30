"""Contract tests for Feature-020 — POST /tasks/athlete-preprocessing (T028).

Covers contracts/submit_athlete_preprocessing.md 的 7 个断言点:
  1. 单条成功：status='running' + reused=false + job_id UUID
  2. 单条重复（未 force）→ reused=true + 返回原 job_id
  3. force=true 对已成功 job → supersede + 新 job_id
  4. 批量 3 条含 1 条 ID 不存在 → rejected 含 ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND
  5. 批量 items=[] → 422 VALIDATION_FAILED
  6. 通道满 → 503 CHANNEL_QUEUE_FULL（整批原子拒绝）
  7. 请求体路径隔离（单体/批量 schema 互不兼容）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.errors import AppException, ErrorCode
from src.api.main import app
from src.utils.time_utils import now_cst
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


@dataclass
class _SingleOutcome:
    job_id: UUID
    athlete_video_classification_id: UUID
    cos_object_key: str
    status: str
    reused: bool
    segment_count: int | None
    has_audio: bool
    started_at: datetime
    completed_at: datetime | None
    task_id: UUID | None = None


@dataclass
class _BatchSubmitted:
    athlete_video_classification_id: UUID
    job_id: UUID
    reused: bool
    task_id: UUID | None = None


@dataclass
class _BatchRejected:
    athlete_video_classification_id: UUID
    error_code: str
    message: str


@dataclass
class _BatchOutcome:
    submitted: list
    rejected: list


@pytest.mark.contract
class TestAthletePreprocessingSubmitContract:

    def test_c1_single_new_job(self, client):
        avc_id = uuid4()
        outcome = _SingleOutcome(
            job_id=uuid4(),
            athlete_video_classification_id=avc_id,
            cos_object_key="charhuang/tt_video/athletes/X/a.mp4",
            status="running",
            reused=False,
            segment_count=None,
            has_audio=False,
            started_at=now_cst(),
            completed_at=None,
            task_id=uuid4(),
        )
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_preprocessing",
            new_callable=AsyncMock, return_value=outcome,
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-preprocessing",
                json={"athlete_video_classification_id": str(avc_id), "force": False},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["status"] == "running"
        assert data["reused"] is False
        UUID(data["job_id"])

    def test_c2_duplicate_returns_reused(self, client):
        avc_id = uuid4()
        existing_job_id = uuid4()
        outcome = _SingleOutcome(
            job_id=existing_job_id,
            athlete_video_classification_id=avc_id,
            cos_object_key="charhuang/x.mp4",
            status="success",
            reused=True,
            segment_count=4,
            has_audio=True,
            started_at=now_cst(),
            completed_at=now_cst(),
        )
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_preprocessing",
            new_callable=AsyncMock, return_value=outcome,
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-preprocessing",
                json={"athlete_video_classification_id": str(avc_id), "force": False},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["reused"] is True
        assert data["job_id"] == str(existing_job_id)

    def test_c3_force_supersede_new_job(self, client):
        avc_id = uuid4()
        new_job = uuid4()
        outcome = _SingleOutcome(
            job_id=new_job,
            athlete_video_classification_id=avc_id,
            cos_object_key="charhuang/x.mp4",
            status="running",
            reused=False,
            segment_count=None,
            has_audio=False,
            started_at=now_cst(),
            completed_at=None,
        )
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_preprocessing",
            new_callable=AsyncMock, return_value=outcome,
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-preprocessing",
                json={"athlete_video_classification_id": str(avc_id), "force": True},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["job_id"] == str(new_job)
        assert data["reused"] is False

    def test_c4_batch_with_rejected_item(self, client):
        ok1, ok2, missing = uuid4(), uuid4(), uuid4()
        outcome = _BatchOutcome(
            submitted=[
                _BatchSubmitted(ok1, uuid4(), reused=False),
                _BatchSubmitted(ok2, uuid4(), reused=True),
            ],
            rejected=[
                _BatchRejected(
                    athlete_video_classification_id=missing,
                    error_code="ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND",
                    message="not found",
                ),
            ],
        )
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_preprocessing_batch",
            new_callable=AsyncMock, return_value=outcome,
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-preprocessing/batch",
                json={"items": [
                    {"athlete_video_classification_id": str(ok1)},
                    {"athlete_video_classification_id": str(ok2)},
                    {"athlete_video_classification_id": str(missing)},
                ]},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert len(data["submitted"]) == 2
        assert len(data["rejected"]) == 1
        assert data["rejected"][0]["error_code"] == "ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND"

    def test_c5_empty_batch_rejected(self, client):
        resp = client.post(
            "/api/v1/tasks/athlete-preprocessing/batch",
            json={"items": []},
        )
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")

    def test_c6_channel_full_atomic_rejection(self, client):
        avc_id = uuid4()
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_preprocessing",
            new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.CHANNEL_QUEUE_FULL,
                details={"channel": "preprocessing"},
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-preprocessing",
                json={"athlete_video_classification_id": str(avc_id)},
            )
        assert resp.status_code == 503, resp.text
        assert_error_envelope(resp.json(), code="CHANNEL_QUEUE_FULL")

    def test_c7_single_endpoint_rejects_batch_body(self, client):
        resp = client.post(
            "/api/v1/tasks/athlete-preprocessing",
            json={"items": [{"athlete_video_classification_id": str(uuid4())}]},
        )
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")
