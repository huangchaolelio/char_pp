"""Feature-021 T021 — POST /api/v1/tasks/curation 合约测试.

覆盖 contracts/submit_curation.md 全部 10 条用例（service 层用 mock）：
1. 单条 happy-path
2. 单条幂等短路（idempotent_short_circuit=True）
3. 单条 force=true 新建
4. 单条 classification_id 不存在 → 404 NOT_FOUND
5. 单条预处理未完成 → 404 PREPROCESSING_JOB_NOT_FOUND
6. 单条 rubric_version 不存在 → 404 RUBRIC_VERSION_NOT_FOUND
7. 单条 RUBRIC_INVALID → 422
8. 单条 CURATION_RUBRIC_MISMATCH（version 不一致 + force=false）→ 409
9. 批量 3 条混合（成功/短路/拒绝）→ submitted/rejected 分桶正确
10. 批量超 100 → 422 VALIDATION_FAILED（Pydantic max_length=100）
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
class _SingleOutcome:
    job_id: UUID
    task_id: UUID | None
    cos_object_key: str
    curation_rubric_version: str
    status: str
    queued: bool
    idempotent_short_circuit: bool


@dataclass
class _BSubmitted:
    coach_video_classification_id: UUID
    job_id: UUID | None
    task_id: UUID | None
    queued: bool
    idempotent_short_circuit: bool


@dataclass
class _BRejected:
    coach_video_classification_id: UUID
    error_code: str
    message: str


@dataclass
class _BatchOut:
    submitted: list
    rejected: list


_PATCH_SINGLE = "src.api.routers.curation_jobs.submit_curation"
_PATCH_BATCH = "src.api.routers.curation_jobs.submit_curation_batch"


@pytest.mark.contract
class TestSubmitCurationSingleContract:

    def test_c1_single_happy_path(self, client):
        cid, jid, tid = uuid4(), uuid4(), uuid4()
        out = _SingleOutcome(
            job_id=jid, task_id=tid, cos_object_key="x/y/z.mp4",
            curation_rubric_version="v1", status="pending",
            queued=True, idempotent_short_circuit=False,
        )
        with patch(_PATCH_SINGLE, new_callable=AsyncMock, return_value=out):
            resp = client.post(
                "/api/v1/tasks/curation",
                json={"coach_video_classification_id": str(cid)},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["job_id"] == str(jid)
        assert data["task_id"] == str(tid)
        assert data["queued"] is True
        assert data["idempotent_short_circuit"] is False
        assert data["curation_rubric_version"] == "v1"

    def test_c2_idempotent_short_circuit(self, client):
        cid, jid = uuid4(), uuid4()
        out = _SingleOutcome(
            job_id=jid, task_id=None, cos_object_key="x/y/z.mp4",
            curation_rubric_version="v1", status="success",
            queued=False, idempotent_short_circuit=True,
        )
        with patch(_PATCH_SINGLE, new_callable=AsyncMock, return_value=out):
            resp = client.post(
                "/api/v1/tasks/curation",
                json={"coach_video_classification_id": str(cid)},
            )
        data = assert_success_envelope(resp.json())
        assert data["idempotent_short_circuit"] is True
        assert data["queued"] is False
        assert data["task_id"] is None

    def test_c3_force_true_creates_new_job(self, client):
        cid, jid, tid = uuid4(), uuid4(), uuid4()
        out = _SingleOutcome(
            job_id=jid, task_id=tid, cos_object_key="x/y/z.mp4",
            curation_rubric_version="v1", status="pending",
            queued=True, idempotent_short_circuit=False,
        )
        with patch(_PATCH_SINGLE, new_callable=AsyncMock, return_value=out) as mock_submit:
            resp = client.post(
                "/api/v1/tasks/curation",
                json={
                    "coach_video_classification_id": str(cid),
                    "force": True,
                },
            )
        assert resp.status_code == 200, resp.text
        # 验证 force=True 透传到 service 层
        kwargs = mock_submit.call_args.kwargs
        assert kwargs["force"] is True

    def test_c4_classification_not_found(self, client):
        cid = uuid4()
        with patch(
            _PATCH_SINGLE, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.NOT_FOUND, details={"resource_id": str(cid)}
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/curation",
                json={"coach_video_classification_id": str(cid)},
            )
        assert resp.status_code == 404, resp.text
        assert_error_envelope(resp.json(), code="NOT_FOUND")

    def test_c5_preprocessing_not_complete(self, client):
        cid = uuid4()
        with patch(
            _PATCH_SINGLE, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.PREPROCESSING_JOB_NOT_FOUND,
                details={"coach_video_classification_id": str(cid)},
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/curation",
                json={"coach_video_classification_id": str(cid)},
            )
        assert resp.status_code == 404, resp.text
        assert_error_envelope(resp.json(), code="PREPROCESSING_JOB_NOT_FOUND")

    def test_c6_rubric_version_not_found(self, client):
        cid = uuid4()
        with patch(
            _PATCH_SINGLE, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.RUBRIC_VERSION_NOT_FOUND, details={"version": "v999"}
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/curation",
                json={
                    "coach_video_classification_id": str(cid),
                    "curation_rubric_version": "v999",
                },
            )
        assert resp.status_code == 404, resp.text
        assert_error_envelope(resp.json(), code="RUBRIC_VERSION_NOT_FOUND")

    def test_c7_rubric_invalid(self, client):
        cid = uuid4()
        with patch(
            _PATCH_SINGLE, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.RUBRIC_INVALID,
                details={"version": "v1", "schema_errors": [{"path": "<root>", "message": "..."}]},
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/curation",
                json={"coach_video_classification_id": str(cid)},
            )
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="RUBRIC_INVALID")

    def test_c8_rubric_mismatch_without_force(self, client):
        cid = uuid4()
        with patch(
            _PATCH_SINGLE, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.CURATION_RUBRIC_MISMATCH,
                details={
                    "existing_rubric_version": "v1",
                    "submitted_rubric_version": "v2",
                },
            ),
        ):
            resp = client.post(
                "/api/v1/tasks/curation",
                json={
                    "coach_video_classification_id": str(cid),
                    "curation_rubric_version": "v2",
                },
            )
        assert resp.status_code == 409, resp.text
        err = assert_error_envelope(resp.json(), code="CURATION_RUBRIC_MISMATCH")
        assert err["details"]["existing_rubric_version"] == "v1"
        assert err["details"]["submitted_rubric_version"] == "v2"


@pytest.mark.contract
class TestSubmitCurationBatchContract:

    def test_c9_batch_mixed_outcome(self, client):
        cid_ok, cid_short, cid_bad = uuid4(), uuid4(), uuid4()
        jid_ok, tid_ok = uuid4(), uuid4()
        jid_short = uuid4()
        out = _BatchOut(
            submitted=[
                _BSubmitted(cid_ok, jid_ok, tid_ok, True, False),
                _BSubmitted(cid_short, jid_short, None, False, True),
            ],
            rejected=[
                _BRejected(cid_bad, "PREPROCESSING_JOB_NOT_FOUND", "..."),
            ],
        )
        with patch(_PATCH_BATCH, new_callable=AsyncMock, return_value=out):
            resp = client.post(
                "/api/v1/tasks/curation/batch",
                json={"items": [
                    {"coach_video_classification_id": str(cid_ok)},
                    {"coach_video_classification_id": str(cid_short)},
                    {"coach_video_classification_id": str(cid_bad)},
                ]},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert len(data["submitted"]) == 2
        assert len(data["rejected"]) == 1
        assert data["rejected"][0]["error_code"] == "PREPROCESSING_JOB_NOT_FOUND"

    def test_c10_batch_too_large_validation(self, client):
        """超过 max_length=100 应直接 422 VALIDATION_FAILED（Pydantic 拦截）。"""
        items = [{"coach_video_classification_id": str(uuid4())} for _ in range(101)]
        resp = client.post("/api/v1/tasks/curation/batch", json={"items": items})
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")
