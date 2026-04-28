"""Contract tests for Feature 013 US2 batch submission endpoints.

Covers:
  * POST /api/v1/tasks/classification/batch
  * POST /api/v1/tasks/kb-extraction/batch
  * POST /api/v1/tasks/diagnosis/batch

All three share the same SubmissionResult contract (shape defined in
``specs/013-task-pipeline-redesign/contracts/task_submit.yaml``); tests here
mock the service layer and assert request/response wire shape, including:
  * Happy-path 200 with mixed partial-success items.
  * 400 BATCH_TOO_LARGE when item count > settings.batch_max_size.
  * 422 when items is empty or items[].cos_object_key missing.
  * kb-extraction batch: per-item CLASSIFICATION_REQUIRED rejection merged
    into response with original index preserved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from src.utils.time_utils import now_cst
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.models.analysis_task import TaskType
from src.services.task_channel_service import ChannelLiveSnapshot
from src.services.task_submission_service import (
    BatchTooLargeError,
    SubmissionBatchResult,
    SubmissionOutcome,
)


pytestmark = pytest.mark.contract


def _snap(task_type: TaskType, *, pending: int = 0, cap: int = 5) -> ChannelLiveSnapshot:
    return ChannelLiveSnapshot(
        task_type=task_type,
        queue_capacity=cap,
        concurrency=1,
        current_pending=pending,
        current_processing=0,
        remaining_slots=max(0, cap - pending),
        enabled=True,
        recent_completion_rate_per_min=0.0,
    )


def _batch_result(
    task_type: TaskType,
    *,
    accepted: int,
    rejected: int,
    rejection_code: str = "QUEUE_FULL",
    cap: int = 5,
) -> SubmissionBatchResult:
    outcomes: list[SubmissionOutcome] = []
    for i in range(accepted):
        outcomes.append(
            SubmissionOutcome(
                index=i, accepted=True, task_id=uuid4(), cos_object_key=f"v_{i}.mp4"
            )
        )
    for j in range(rejected):
        outcomes.append(
            SubmissionOutcome(
                index=accepted + j,
                accepted=False,
                cos_object_key=f"v_{accepted + j}.mp4",
                rejection_code=rejection_code,
                rejection_message=f"{rejection_code} test",
            )
        )
    return SubmissionBatchResult(
        task_type=task_type,
        accepted=accepted,
        rejected=rejected,
        items=outcomes,
        channel=_snap(task_type, pending=accepted, cap=cap),
        submitted_at=now_cst(),
    )


@pytest.fixture
def client(db_no_op):
    return TestClient(app)


@pytest.fixture
def db_no_op():
    from src.db.session import get_db

    async def _fake_db():  # pragma: no cover
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


# ──────────────────────────────────────────────────────────────────────────────
# /tasks/classification/batch
# ──────────────────────────────────────────────────────────────────────────────


class TestClassificationBatchContract:
    def test_batch_happy_partial_success(self, client):
        """3 accepted + 2 rejected (QUEUE_FULL) → 200 with both lists in items."""
        with patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls:
            inst = SvcCls.return_value
            inst.submit_batch = AsyncMock(
                return_value=_batch_result(
                    TaskType.video_classification, accepted=3, rejected=2
                )
            )
            response = client.post(
                "/api/v1/tasks/classification/batch",
                json={
                    "items": [
                        {"cos_object_key": f"v_{i}.mp4"} for i in range(5)
                    ]
                },
            )
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["task_type"] == "video_classification"
        assert body["accepted"] == 3
        assert body["rejected"] == 2
        assert len(body["items"]) == 5
        assert {it["accepted"] for it in body["items"]} == {True, False}
        rejected_codes = {it["rejection_code"] for it in body["items"] if not it["accepted"]}
        assert rejected_codes == {"QUEUE_FULL"}
        assert "channel" in body and body["channel"]["queue_capacity"] == 5

    def test_batch_too_large_returns_400(self, client):
        """Service raises BatchTooLargeError → 400 BATCH_TOO_LARGE."""
        with patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls:
            inst = SvcCls.return_value
            inst.submit_batch = AsyncMock(
                side_effect=BatchTooLargeError("batch size 101 exceeds max 100")
            )
            response = client.post(
                "/api/v1/tasks/classification/batch",
                json={"items": [{"cos_object_key": f"v_{i}.mp4"} for i in range(2)]},
            )
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "BATCH_TOO_LARGE"

    def test_empty_items_returns_422(self, client):
        """min_length=1 on items → 422."""
        response = client.post(
            "/api/v1/tasks/classification/batch", json={"items": []}
        )
        assert response.status_code == 422

    def test_missing_cos_object_key_in_item_returns_422(self, client):
        response = client.post(
            "/api/v1/tasks/classification/batch",
            json={"items": [{"force": True}]},
        )
        assert response.status_code == 422


# ──────────────────────────────────────────────────────────────────────────────
# /tasks/kb-extraction/batch
# ──────────────────────────────────────────────────────────────────────────────


class TestKbExtractionBatchContract:
    def test_batch_all_classified_happy(self, client):
        """All items pass the gate → passed through to service."""
        with patch(
            "src.api.routers.tasks._F13ClassificationGateService"
        ) as GateCls, patch(
            "src.api.routers.tasks._F13TaskSubmissionService"
        ) as SvcCls:
            gate = GateCls.return_value
            gate.check_classified = AsyncMock(return_value=True)
            gate.get_tech_category = AsyncMock(return_value="forehand_loop_fast")

            inst = SvcCls.return_value
            inst.submit_batch = AsyncMock(
                return_value=_batch_result(TaskType.kb_extraction, accepted=2, rejected=0)
            )

            response = client.post(
                "/api/v1/tasks/kb-extraction/batch",
                json={
                    "items": [
                        {"cos_object_key": "a.mp4"},
                        {"cos_object_key": "b.mp4"},
                    ]
                },
            )
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["accepted"] == 2
        assert body["rejected"] == 0
        assert body["task_type"] == "kb_extraction"

    def test_batch_mixed_classification_gate(self, client):
        """Index 0 & 2 classified; index 1 not → merged response with original indices preserved."""
        with patch(
            "src.api.routers.tasks._F13ClassificationGateService"
        ) as GateCls, patch(
            "src.api.routers.tasks._F13TaskSubmissionService"
        ) as SvcCls:
            gate = GateCls.return_value

            async def _check(_db, key):
                return key != "unclassified.mp4"

            async def _get(_db, key):
                return None if key == "unclassified.mp4" else "forehand_loop_fast"

            gate.check_classified = AsyncMock(side_effect=_check)
            gate.get_tech_category = AsyncMock(side_effect=_get)

            # Service receives only the 2 classified items — they arrive with
            # service-local indices 0 and 1; the router remaps back to 0 and 2.
            classified_outcomes = [
                SubmissionOutcome(
                    index=0, accepted=True, task_id=uuid4(), cos_object_key="a.mp4"
                ),
                SubmissionOutcome(
                    index=1, accepted=True, task_id=uuid4(), cos_object_key="c.mp4"
                ),
            ]
            inst = SvcCls.return_value
            inst.submit_batch = AsyncMock(
                return_value=SubmissionBatchResult(
                    task_type=TaskType.kb_extraction,
                    accepted=2,
                    rejected=0,
                    items=classified_outcomes,
                    channel=_snap(TaskType.kb_extraction, pending=2, cap=50),
                    submitted_at=now_cst(),
                )
            )

            response = client.post(
                "/api/v1/tasks/kb-extraction/batch",
                json={
                    "items": [
                        {"cos_object_key": "a.mp4"},
                        {"cos_object_key": "unclassified.mp4"},
                        {"cos_object_key": "c.mp4"},
                    ]
                },
            )

        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["accepted"] == 2
        assert body["rejected"] == 1

        # Items must be ordered by original index 0,1,2.
        items = body["items"]
        assert [it["index"] for it in items] == [0, 1, 2]
        assert items[0]["accepted"] is True
        assert items[1]["accepted"] is False
        assert items[1]["rejection_code"] == "CLASSIFICATION_REQUIRED"
        assert items[2]["accepted"] is True

    def test_batch_all_unclassified_returns_200_all_rejected(self, client):
        """Every item fails gate → no service call, 200 response with all items rejected."""
        with patch(
            "src.api.routers.tasks._F13ClassificationGateService"
        ) as GateCls, patch(
            "src.api.routers.tasks._F13TaskSubmissionService"
        ) as SvcCls:
            gate = GateCls.return_value
            gate.check_classified = AsyncMock(return_value=False)
            gate.get_tech_category = AsyncMock(return_value=None)

            # Snapshot is still fetched via the service instance.
            inst = SvcCls.return_value
            inst._channels.get_snapshot = AsyncMock(
                return_value=_snap(TaskType.kb_extraction, pending=0, cap=50)
            )
            inst.submit_batch = AsyncMock()  # must NOT be called

            response = client.post(
                "/api/v1/tasks/kb-extraction/batch",
                json={
                    "items": [
                        {"cos_object_key": "a.mp4"},
                        {"cos_object_key": "b.mp4"},
                    ]
                },
            )

        assert response.status_code == 200
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["accepted"] == 0
        assert body["rejected"] == 2
        assert all(
            it["rejection_code"] == "CLASSIFICATION_REQUIRED" for it in body["items"]
        )
        inst.submit_batch.assert_not_called()

    def test_batch_too_large_short_circuits_before_gate(self, client):
        """101-item payload → 400 BATCH_TOO_LARGE without calling the gate."""
        from src.config import get_settings
        max_size = get_settings().batch_max_size

        with patch(
            "src.api.routers.tasks._F13ClassificationGateService"
        ) as GateCls:
            gate = GateCls.return_value
            gate.check_classified = AsyncMock(return_value=True)

            response = client.post(
                "/api/v1/tasks/kb-extraction/batch",
                json={
                    "items": [
                        {"cos_object_key": f"v_{i}.mp4"} for i in range(max_size + 1)
                    ]
                },
            )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "BATCH_TOO_LARGE"
        gate.check_classified.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# /tasks/diagnosis/batch
# ──────────────────────────────────────────────────────────────────────────────


class TestDiagnosisBatchContract:
    def test_batch_happy(self, client):
        with patch(
            "src.api.routers.tasks._F13TaskSubmissionService"
        ) as SvcCls:
            inst = SvcCls.return_value
            inst.submit_batch = AsyncMock(
                return_value=_batch_result(
                    TaskType.athlete_diagnosis, accepted=2, rejected=0, cap=20
                )
            )
            response = client.post(
                "/api/v1/tasks/diagnosis/batch",
                json={
                    "items": [
                        {"video_storage_uri": "athlete/v1.mp4"},
                        {"video_storage_uri": "athlete/v2.mp4",
                         "knowledge_base_version": "v1"},
                    ]
                },
            )
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["accepted"] == 2
        assert body["rejected"] == 0

    def test_batch_missing_video_uri_returns_422(self, client):
        response = client.post(
            "/api/v1/tasks/diagnosis/batch",
            json={"items": [{"knowledge_base_version": "v1"}]},
        )
        assert response.status_code == 422

    def test_batch_empty_items_returns_422(self, client):
        response = client.post("/api/v1/tasks/diagnosis/batch", json={"items": []})
        assert response.status_code == 422
