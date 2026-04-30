"""Contract tests for Feature-020 — POST /tasks/athlete-diagnosis (T036).

Covers contracts/submit_athlete_diagnosis.md 的 9 个断言点:
  1. 正常单条：200 + SuccessEnvelope + tech_category
  2. 素材 ID 不存在 → 404 ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND
  3. 素材未预处理 → 409 ATHLETE_VIDEO_NOT_PREPROCESSED
  4. 无 active standard → 409 STANDARD_NOT_AVAILABLE
  5. analysis_tasks 行 task_type/business_phase/business_step 正确
  6. 批量 3 条（1 条无预处理）→ rejected 含正确 code，其余正常入队
  7. 批量 5 条但通道剩余 3 槽 → 503 CHANNEL_QUEUE_FULL 整批原子拒绝
  8. 重复提交同一 ID 两次 → 生成两条独立 analysis_tasks
  9. 诊断完成后 diagnosis_reports 含三要素（集成测试范畴，合约仅断言 schema）
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.errors import AppException, ErrorCode
from src.api.main import app
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
class _DiagOutcome:
    task_id: UUID
    athlete_video_classification_id: UUID
    tech_category: str
    status: str


@dataclass
class _BSubmitted:
    athlete_video_classification_id: UUID
    task_id: UUID
    reused: bool = False
    job_id: UUID | None = None


@dataclass
class _BRejected:
    athlete_video_classification_id: UUID
    error_code: str
    message: str


@dataclass
class _BatchOut:
    submitted: list
    rejected: list


@pytest.mark.contract
class TestAthleteDiagnosisSubmitContract:

    def test_c1_single_happy_path(self, client):
        avc_id = uuid4()
        tid = uuid4()
        out = _DiagOutcome(tid, avc_id, "forehand_attack", "pending")
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_diagnosis",
            new_callable=AsyncMock, return_value=out,
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-diagnosis",
                json={"athlete_video_classification_id": str(avc_id)},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["task_id"] == str(tid)
        assert data["tech_category"] == "forehand_attack"

    def test_c2_classification_not_found(self, client):
        avc_id = uuid4()
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_diagnosis",
            new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND,
                details={"resource_id": str(avc_id)},
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-diagnosis",
                json={"athlete_video_classification_id": str(avc_id)},
            )
        assert resp.status_code == 404, resp.text
        assert_error_envelope(resp.json(), code="ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND")

    def test_c3_not_preprocessed(self, client):
        avc_id = uuid4()
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_diagnosis",
            new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.ATHLETE_VIDEO_NOT_PREPROCESSED,
                details={
                    "athlete_video_classification_id": str(avc_id),
                    "cos_object_key": "charhuang/x.mp4",
                },
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-diagnosis",
                json={"athlete_video_classification_id": str(avc_id)},
            )
        assert resp.status_code == 409, resp.text
        err = assert_error_envelope(resp.json(), code="ATHLETE_VIDEO_NOT_PREPROCESSED")
        assert err["details"]["athlete_video_classification_id"] == str(avc_id)

    def test_c4_standard_not_available(self, client):
        avc_id = uuid4()
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_diagnosis",
            new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.STANDARD_NOT_AVAILABLE,
                details={"tech_category": "forehand_attack"},
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-diagnosis",
                json={"athlete_video_classification_id": str(avc_id)},
            )
        assert resp.status_code == 409, resp.text
        err = assert_error_envelope(resp.json(), code="STANDARD_NOT_AVAILABLE")
        assert err["details"]["tech_category"] == "forehand_attack"

    def test_c5_analysis_tasks_row_shape(self, client):
        """服务层 outcome 必须暴露 task_id + tech_category（phase/step 由钩子派生，不属响应 schema）."""
        avc_id = uuid4()
        tid = uuid4()
        out = _DiagOutcome(tid, avc_id, "serve", "pending")
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_diagnosis",
            new_callable=AsyncMock, return_value=out,
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-diagnosis",
                json={"athlete_video_classification_id": str(avc_id)},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert "task_id" in data and "tech_category" in data
        assert data["status"] == "pending"

    def test_c6_batch_with_one_not_preprocessed(self, client):
        ok1, ok2, bad = uuid4(), uuid4(), uuid4()
        out = _BatchOut(
            submitted=[
                _BSubmitted(ok1, uuid4()),
                _BSubmitted(ok2, uuid4()),
            ],
            rejected=[
                _BRejected(bad, "ATHLETE_VIDEO_NOT_PREPROCESSED", "not preprocessed"),
            ],
        )
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_diagnosis_batch",
            new_callable=AsyncMock, return_value=out,
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-diagnosis/batch",
                json={"items": [
                    {"athlete_video_classification_id": str(ok1)},
                    {"athlete_video_classification_id": str(ok2)},
                    {"athlete_video_classification_id": str(bad)},
                ]},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert len(data["submitted"]) == 2
        assert len(data["rejected"]) == 1
        assert data["rejected"][0]["error_code"] == "ATHLETE_VIDEO_NOT_PREPROCESSED"

    def test_c7_batch_channel_full_atomic(self, client):
        """批量 5 条 + 通道剩余 < 5 → 503 整批拒绝."""
        ids = [uuid4() for _ in range(5)]
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_diagnosis_batch",
            new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.CHANNEL_QUEUE_FULL,
                details={"channel": "diagnosis", "requested": 5, "remaining": 3},
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/athlete-diagnosis/batch",
                json={"items": [
                    {"athlete_video_classification_id": str(i)} for i in ids
                ]},
            )
        assert resp.status_code == 503, resp.text
        assert_error_envelope(resp.json(), code="CHANNEL_QUEUE_FULL")

    def test_c8_duplicate_submit_two_distinct_tasks(self, client):
        """同一 avc_id 重复提交返回不同 task_id（Q3 决议：每次新报告）."""
        avc_id = uuid4()
        tids = [uuid4(), uuid4()]
        outcomes = [
            _DiagOutcome(tids[0], avc_id, "forehand_attack", "pending"),
            _DiagOutcome(tids[1], avc_id, "forehand_attack", "pending"),
        ]
        results: list[UUID] = []
        with patch(
            "src.api.routers.athlete_tasks.submit_athlete_diagnosis",
            new_callable=AsyncMock, side_effect=outcomes,
        ):
            for _ in range(2):
                resp = client.post(
                    "/api/v1/tasks/athlete-diagnosis",
                    json={"athlete_video_classification_id": str(avc_id)},
                )
                assert resp.status_code == 200, resp.text
                results.append(UUID(resp.json()["data"]["task_id"]))
        assert results[0] != results[1]

    def test_c9_request_schema_forbids_extra(self, client):
        """extra='forbid' 必须生效."""
        resp = client.post(
            "/api/v1/tasks/athlete-diagnosis",
            json={"athlete_video_classification_id": str(uuid4()), "rogue": 1},
        )
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")
