"""Contract tests for Feature-016 preprocessing API.

Covers contracts/submit_preprocessing.md (C1-C7),
contracts/submit_preprocessing_batch.md (C1-C5),
and contracts/get_preprocessing_job.md (C1-C6).

All tests mock the service layer so the router contract stays isolated
from DB / Celery / COS concerns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def db_no_op():
    from src.db.session import get_db

    async def _fake_db():  # pragma: no cover — yield-and-done
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(db_no_op):
    return TestClient(app)


# ── Helper builders ─────────────────────────────────────────────────────────

def _submit_outcome(job_id: UUID, *, reused: bool, status: str = "running",
                    cos_object_key: str = "coach/video.mp4"):
    """Shape the dataclass returned by preprocessing_service.create_or_reuse."""
    from dataclasses import dataclass

    @dataclass
    class Out:
        job_id: UUID
        status: str
        reused: bool
        cos_object_key: str
        segment_count: int | None
        has_audio: bool | None
        started_at: datetime
        completed_at: datetime | None

    return Out(
        job_id=job_id, status=status, reused=reused,
        cos_object_key=cos_object_key,
        segment_count=4 if reused else None,
        has_audio=True if reused else None,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc) if reused else None,
    )


def _batch_item_outcome(cos_object_key: str, *, ok: bool = True,
                        reused: bool = False, error_code: str | None = None):
    from dataclasses import dataclass

    @dataclass
    class Item:
        cos_object_key: str
        job_id: UUID | None
        status: str | None
        reused: bool
        error_code: str | None
        error_message: str | None

    if ok:
        return Item(cos_object_key=cos_object_key, job_id=uuid4(),
                    status="success" if reused else "running",
                    reused=reused, error_code=None, error_message=None)
    return Item(cos_object_key=cos_object_key, job_id=None, status=None,
                reused=False,
                error_code=error_code or "COS_KEY_NOT_CLASSIFIED",
                error_message=f"{cos_object_key} not classified")


# ── submit_preprocessing.md ─────────────────────────────────────────────────

@pytest.mark.contract
class TestPreprocessingSubmitContract:
    """contracts/submit_preprocessing.md C1-C7."""

    def test_c1_happy_path_new_job_returns_job_metadata(self, client):
        """C1: valid cos_object_key with force=false submits a new running job."""
        outcome = _submit_outcome(uuid4(), reused=False)
        with patch(
            "src.api.routers.tasks._preprocessing_service.create_or_reuse",
            new_callable=AsyncMock, return_value=outcome,
        ) as mocked_create, patch(
            "src.api.routers.tasks._preprocessing_enqueue_task"
        ) as mocked_enqueue:
            response = client.post(
                "/api/v1/tasks/preprocessing",
                json={"cos_object_key": "coach/forehand.mp4", "force": False},
            )
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["job_id"] == str(outcome.job_id)
        assert body["status"] == "running"
        assert body["reused"] is False
        assert body["cos_object_key"] == "coach/video.mp4"
        mocked_create.assert_awaited_once()
        # New (reused=False) jobs must be enqueued; reused jobs must not.
        mocked_enqueue.assert_called_once()

    def test_c3_force_false_reused_returns_200_with_reused_true(self, client):
        """C3: force=false + existing success job → reused=true, no new enqueue."""
        outcome = _submit_outcome(uuid4(), reused=True, status="success")
        with patch(
            "src.api.routers.tasks._preprocessing_service.create_or_reuse",
            new_callable=AsyncMock, return_value=outcome,
        ), patch(
            "src.api.routers.tasks._preprocessing_enqueue_task"
        ) as mocked_enqueue:
            response = client.post(
                "/api/v1/tasks/preprocessing",
                json={"cos_object_key": "coach/video.mp4"},
            )
        assert response.status_code == 200
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["reused"] is True
        assert body["status"] == "success"
        assert body["segment_count"] == 4
        assert body["has_audio"] is True
        mocked_enqueue.assert_not_called()

    def test_c2_cos_key_not_classified_returns_400(self, client):
        """C2: unknown cos_object_key → 400 COS_KEY_NOT_CLASSIFIED."""
        from src.services.preprocessing_service import CosKeyNotClassifiedError

        with patch(
            "src.api.routers.tasks._preprocessing_service.create_or_reuse",
            new_callable=AsyncMock,
            side_effect=CosKeyNotClassifiedError("missing/video.mp4"),
        ):
            response = client.post(
                "/api/v1/tasks/preprocessing",
                json={"cos_object_key": "missing/video.mp4"},
            )
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "COS_KEY_NOT_CLASSIFIED"

    def test_c4_force_true_creates_new_job_id(self, client):
        """C4: force=true triggers a new running job, old one superseded in service."""
        outcome = _submit_outcome(uuid4(), reused=False)
        with patch(
            "src.api.routers.tasks._preprocessing_service.create_or_reuse",
            new_callable=AsyncMock, return_value=outcome,
        ), patch(
            "src.api.routers.tasks._preprocessing_enqueue_task"
        ) as mocked_enqueue:
            response = client.post(
                "/api/v1/tasks/preprocessing",
                json={"cos_object_key": "coach/video.mp4", "force": True},
            )
        assert response.status_code == 200
        envelope = response.json()
        assert envelope["success"] is True
        assert envelope["data"]["status"] == "running"
        mocked_enqueue.assert_called_once()

    def test_c6_channel_queue_full_returns_503(self, client):
        """Feature-017：CHANNEL_QUEUE_FULL 按章程 v1.4.0 归类为 503（服务不可用）."""
        from src.services.preprocessing_service import ChannelQueueFullError

        with patch(
            "src.api.routers.tasks._preprocessing_service.create_or_reuse",
            new_callable=AsyncMock,
            side_effect=ChannelQueueFullError("preprocessing"),
        ):
            response = client.post(
                "/api/v1/tasks/preprocessing",
                json={"cos_object_key": "coach/video.mp4"},
            )
        assert response.status_code == 503
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "CHANNEL_QUEUE_FULL"

    def test_c7_missing_cos_object_key_returns_422(self, client):
        response = client.post("/api/v1/tasks/preprocessing", json={})
        assert response.status_code == 422


# ── submit_preprocessing_batch.md ───────────────────────────────────────────

@pytest.mark.contract
class TestPreprocessingBatchSubmitContract:
    """contracts/submit_preprocessing_batch.md C1-C5."""

    def test_c1_batch_all_valid(self, client):
        results = [
            _batch_item_outcome("a.mp4"),
            _batch_item_outcome("b.mp4"),
            _batch_item_outcome("c.mp4", reused=True),
        ]
        with patch(
            "src.api.routers.tasks._preprocessing_service.create_or_reuse_batch",
            new_callable=AsyncMock, return_value=results,
        ), patch(
            "src.api.routers.tasks._preprocessing_enqueue_task"
        ):
            response = client.post(
                "/api/v1/tasks/preprocessing/batch",
                json={"items": [
                    {"cos_object_key": "a.mp4"},
                    {"cos_object_key": "b.mp4"},
                    {"cos_object_key": "c.mp4"},
                ]},
            )
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["submitted"] == 3
        assert body["reused"] == 1
        assert body["failed"] == 0
        assert len(body["results"]) == 3

    def test_c3_partial_failure_isolated(self, client):
        results = [
            _batch_item_outcome("good.mp4"),
            _batch_item_outcome("bad.mp4", ok=False),
        ]
        with patch(
            "src.api.routers.tasks._preprocessing_service.create_or_reuse_batch",
            new_callable=AsyncMock, return_value=results,
        ), patch(
            "src.api.routers.tasks._preprocessing_enqueue_task"
        ):
            response = client.post(
                "/api/v1/tasks/preprocessing/batch",
                json={"items": [
                    {"cos_object_key": "good.mp4"},
                    {"cos_object_key": "bad.mp4"},
                ]},
            )
        assert response.status_code == 200
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["submitted"] == 1
        assert body["failed"] == 1
        assert body["results"][1]["job_id"] is None
        assert body["results"][1]["error_code"] == "COS_KEY_NOT_CLASSIFIED"

    def test_c2_batch_too_large(self, client):
        from src.services.preprocessing_service import BatchTooLargeError

        with patch(
            "src.api.routers.tasks._preprocessing_service.create_or_reuse_batch",
            new_callable=AsyncMock,
            side_effect=BatchTooLargeError(150, 100),
        ):
            response = client.post(
                "/api/v1/tasks/preprocessing/batch",
                json={"items": [
                    {"cos_object_key": f"x{i}.mp4"} for i in range(3)
                ]},
            )
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "BATCH_TOO_LARGE"

    def test_c4_empty_items_422(self, client):
        response = client.post(
            "/api/v1/tasks/preprocessing/batch", json={"items": []}
        )
        assert response.status_code == 422


# ── get_preprocessing_job.md ────────────────────────────────────────────────

@pytest.mark.contract
class TestPreprocessingGetContract:
    """contracts/get_preprocessing_job.md C1-C6."""

    def test_c1_success_job_full_payload(self, client):
        job_id = uuid4()
        from dataclasses import dataclass, field

        @dataclass
        class _SegView:
            segment_index: int
            start_ms: int
            end_ms: int
            cos_object_key: str
            size_bytes: int

        @dataclass
        class _JobView:
            job_id: UUID
            cos_object_key: str
            status: str
            force: bool
            started_at: datetime
            completed_at: datetime | None
            duration_ms: int | None
            segment_count: int | None
            has_audio: bool
            error_message: str | None
            original_meta: dict | None
            target_standard: dict | None
            audio: dict | None
            segments: list = field(default_factory=list)

        view = _JobView(
            job_id=job_id,
            cos_object_key="coach/video.mp4",
            status="success",
            force=False,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            duration_ms=600000,
            segment_count=4,
            has_audio=True,
            error_message=None,
            original_meta={
                "fps": 25.0, "width": 1920, "height": 1080,
                "duration_ms": 600000, "codec": "h264",
                "size_bytes": 124518400, "has_audio": True,
            },
            target_standard={
                "target_fps": 30, "target_short_side": 720,
                "segment_duration_s": 180,
            },
            audio={"cos_object_key": "preproc/.../audio.wav",
                   "size_bytes": 19200000},
            segments=[_SegView(
                segment_index=i, start_ms=i*180000, end_ms=(i+1)*180000,
                cos_object_key=f"preproc/.../seg_{i:04d}.mp4",
                size_bytes=22_000_000,
            ) for i in range(4)],
        )
        with patch(
            "src.api.routers.video_preprocessing._preprocessing_service.get_job_view",
            new_callable=AsyncMock, return_value=view,
        ):
            response = client.get(f"/api/v1/video-preprocessing/{job_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        # Feature-017：信封化后业务载荷进入 body["data"]。
        assert body["success"] is True
        data = body["data"]
        assert data["job_id"] == str(job_id)
        assert data["status"] == "success"
        assert data["segment_count"] == 4
        assert len(data["segments"]) == 4
        # Segments must be ordered by segment_index.
        assert [s["segment_index"] for s in data["segments"]] == [0, 1, 2, 3]
        assert data["original_meta"]["fps"] == 25.0
        assert data["target_standard"]["target_fps"] == 30
        assert data["audio"]["size_bytes"] == 19200000

    def test_c4_not_found_404(self, client):
        with patch(
            "src.api.routers.video_preprocessing._preprocessing_service.get_job_view",
            new_callable=AsyncMock, return_value=None,
        ):
            response = client.get(f"/api/v1/video-preprocessing/{uuid4()}")
        assert response.status_code == 404
        body = response.json()
        # Feature-017：错误信封断言
        assert body["success"] is False
        assert body["error"]["code"] == "PREPROCESSING_JOB_NOT_FOUND"

    def test_c5_non_uuid_job_id_422(self, client):
        response = client.get("/api/v1/video-preprocessing/not-a-uuid")
        assert response.status_code == 422
