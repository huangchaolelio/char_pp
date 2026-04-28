"""Contract tests for POST /api/v1/tasks/diagnosis (Feature 013 US1)."""

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
        task_type=TaskType.athlete_diagnosis,
        queue_capacity=20, concurrency=2,
        current_pending=1, current_processing=0,
        remaining_slots=19, enabled=True,
        recent_completion_rate_per_min=0.0,
    )
    return SubmissionBatchResult(
        task_type=TaskType.athlete_diagnosis,
        accepted=1, rejected=0,
        items=[SubmissionOutcome(
            index=0, accepted=True, task_id=uuid4(), cos_object_key=None,
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
class TestDiagnosisSubmitContract:
    def test_happy_path_200(self, client):
        with patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls:
            SvcCls.return_value.submit_batch = AsyncMock(return_value=_ok_result())

            response = client.post(
                "/api/v1/tasks/diagnosis",
                json={
                    "video_storage_uri": "https://cos.example.com/athlete/alice.mp4",
                    "knowledge_base_version": "v2026-04-24",
                },
            )

        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["task_type"] == "athlete_diagnosis"
        assert body["accepted"] == 1
        item = body["items"][0]
        # diagnosis tasks don't carry cos_object_key (athlete videos aren't in COS coach dirs)
        assert item["cos_object_key"] is None
        assert item["task_id"] is not None
        ch = body["channel"]
        assert ch["task_type"] == "athlete_diagnosis"
        assert ch["queue_capacity"] == 20

    def test_missing_video_uri_returns_422(self, client):
        response = client.post(
            "/api/v1/tasks/diagnosis",
            json={"knowledge_base_version": "v1"},
        )
        assert response.status_code == 422

    def test_kb_version_optional(self, client):
        with patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls:
            SvcCls.return_value.submit_batch = AsyncMock(return_value=_ok_result())
            response = client.post(
                "/api/v1/tasks/diagnosis",
                json={"video_storage_uri": "s3://bucket/athlete.mp4"},
            )
        assert response.status_code == 200

    def test_extra_field_rejected(self, client):
        response = client.post(
            "/api/v1/tasks/diagnosis",
            json={"video_storage_uri": "x", "foo": 1},
        )
        assert response.status_code == 422
