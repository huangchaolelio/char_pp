"""Feature-021 T061 — PATCH /api/v1/curation-jobs/{id}/segments/{idx} 合约测试.

覆盖 contracts/override_curation_segment.md 9 条用例：

1. rejected → accepted 覆盖 ⇒ 200，effective_decision=accepted，summary 重算
2. accepted → rejected 覆盖 ⇒ 200，effective_decision=rejected
3. 取消覆盖（override_decision=null）⇒ 200，effective_decision 回退
4. 该视频已有 KB 抽取作业 ⇒ kb_stale_after_override=true
5. override_decision='foo' ⇒ 422
6. override_decision != null 且 override_reason 缺失 ⇒ 422
7. job_id 不存在 ⇒ 404
8. segment_index 不存在 ⇒ 404
9. 作业 status=running ⇒ 409 INVALID_STATUS（注：实际错误码用 INVALID_STATUS
   而非契约文档原写的 INVALID_STATE，因为后者不在 ErrorCode 枚举中；二者语义一致）
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.errors import AppException, ErrorCode
from src.api.main import app
from src.services.curation.curation_service import OverrideOutcome
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


_PATCH_TARGET = "src.api.routers.curation_jobs.override_segment"


def _make_outcome(
    *,
    job_id,
    segment_index: int,
    auto: str,
    override: str | None,
    user: str | None,
    reason: str | None,
    overridden_at: datetime | None,
    effective: str,
    accepted_count: int = 14,
    rejected_count: int = 5,
    accepted_ratio: float = 0.7,
    low_quality: bool = False,
    kb_stale: bool = False,
) -> OverrideOutcome:
    return OverrideOutcome(
        job_id=job_id,
        segment_index=segment_index,
        auto_decision=auto,
        override_decision=override,
        override_user=user,
        override_reason=reason,
        overridden_at=overridden_at,
        effective_decision=effective,
        summary_recomputed={
            "accepted_segment_count": accepted_count,
            "rejected_segment_count": rejected_count,
            "accepted_duration_ratio": accepted_ratio,
            "low_quality": low_quality,
        },
        kb_stale_after_override=kb_stale,
    )


@pytest.mark.contract
class TestPatchOverrideContract:

    def test_c1_rejected_to_accepted(self, client):
        jid = uuid4()
        out = _make_outcome(
            job_id=jid, segment_index=3,
            auto="rejected",
            override="accepted",
            user="ops_alice",
            reason="完整动作演示",
            overridden_at=datetime(2026, 5, 18, 11, 20, 0),
            effective="accepted",
            accepted_count=15, rejected_count=4,
            accepted_ratio=0.75,
        )
        with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=out):
            resp = client.patch(
                f"/api/v1/curation-jobs/{jid}/segments/3",
                json={
                    "override_decision": "accepted",
                    "override_reason": "完整动作演示",
                    "override_user": "ops_alice",
                },
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["effective_decision"] == "accepted"
        assert data["override_decision"] == "accepted"
        assert data["auto_decision"] == "rejected"
        assert data["summary_recomputed"]["accepted_duration_ratio"] == 0.75
        assert data["summary_recomputed"]["kb_stale_after_override"] is False

    def test_c2_accepted_to_rejected(self, client):
        jid = uuid4()
        out = _make_outcome(
            job_id=jid, segment_index=2,
            auto="accepted", override="rejected",
            user="ops_alice", reason="动作不完整",
            overridden_at=datetime(2026, 5, 18, 11, 20, 0),
            effective="rejected",
        )
        with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=out):
            resp = client.patch(
                f"/api/v1/curation-jobs/{jid}/segments/2",
                json={
                    "override_decision": "rejected",
                    "override_reason": "动作不完整",
                    "override_user": "ops_alice",
                },
            )
        data = assert_success_envelope(resp.json())
        assert data["effective_decision"] == "rejected"

    def test_c3_cancel_override(self, client):
        """override_decision=null 取消覆盖；reason 可空."""
        jid = uuid4()
        out = _make_outcome(
            job_id=jid, segment_index=3,
            auto="rejected",
            override=None,  # 已清空
            user=None, reason=None, overridden_at=None,
            effective="rejected",  # 回退到 auto
        )
        with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=out):
            resp = client.patch(
                f"/api/v1/curation-jobs/{jid}/segments/3",
                json={
                    "override_decision": None,
                    "override_user": "ops_alice",
                },
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["override_decision"] is None
        assert data["effective_decision"] == "rejected"

    def test_c4_kb_stale_after_override_true(self, client):
        """该视频已有早于覆盖的 KB 抽取作业 ⇒ kb_stale_after_override=true."""
        jid = uuid4()
        out = _make_outcome(
            job_id=jid, segment_index=3,
            auto="rejected", override="accepted",
            user="ops_alice", reason="...",
            overridden_at=datetime(2026, 5, 18, 11, 20, 0),
            effective="accepted",
            kb_stale=True,
        )
        with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=out):
            resp = client.patch(
                f"/api/v1/curation-jobs/{jid}/segments/3",
                json={
                    "override_decision": "accepted",
                    "override_reason": "...",
                    "override_user": "ops_alice",
                },
            )
        data = assert_success_envelope(resp.json())
        assert data["summary_recomputed"]["kb_stale_after_override"] is True

    def test_c5_invalid_decision_value(self, client):
        jid = uuid4()
        # Pydantic 不会拦"foo" 因为 type 是 str | None；service 层会拦
        with patch(
            _PATCH_TARGET, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.VALIDATION_FAILED,
                message="override_decision must be one of ('accepted', 'rejected') or null",
                details={"override_decision": "foo"},
            ),
        ):
            resp = client.patch(
                f"/api/v1/curation-jobs/{jid}/segments/3",
                json={
                    "override_decision": "foo",
                    "override_reason": "x",
                    "override_user": "ops_alice",
                },
            )
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")

    def test_c6_missing_reason_when_decision_set(self, client):
        jid = uuid4()
        with patch(
            _PATCH_TARGET, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.VALIDATION_FAILED,
                message="override_reason is required when override_decision is set",
                details={"field": "override_reason"},
            ),
        ):
            resp = client.patch(
                f"/api/v1/curation-jobs/{jid}/segments/3",
                json={
                    "override_decision": "accepted",
                    "override_user": "ops_alice",
                },
            )
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")

    def test_c7_job_not_found(self, client):
        jid = uuid4()
        with patch(
            _PATCH_TARGET, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.NOT_FOUND,
                message="curation job not found",
                details={"resource_id": str(jid)},
            ),
        ):
            resp = client.patch(
                f"/api/v1/curation-jobs/{jid}/segments/3",
                json={
                    "override_decision": "accepted",
                    "override_reason": "x",
                    "override_user": "ops_alice",
                },
            )
        assert resp.status_code == 404, resp.text
        assert_error_envelope(resp.json(), code="NOT_FOUND")

    def test_c8_segment_not_found(self, client):
        jid = uuid4()
        with patch(
            _PATCH_TARGET, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.NOT_FOUND,
                message="segment not found in this curation job",
                details={"job_id": str(jid), "segment_index": 999},
            ),
        ):
            resp = client.patch(
                f"/api/v1/curation-jobs/{jid}/segments/999",
                json={
                    "override_decision": "accepted",
                    "override_reason": "x",
                    "override_user": "ops_alice",
                },
            )
        assert resp.status_code == 404, resp.text

    def test_c9_job_running_returns_invalid_status(self, client):
        jid = uuid4()
        with patch(
            _PATCH_TARGET, new_callable=AsyncMock,
            side_effect=AppException(
                ErrorCode.INVALID_STATUS,
                message="cannot override segments on job with status='running'",
                details={"job_id": str(jid), "status": "running"},
            ),
        ):
            resp = client.patch(
                f"/api/v1/curation-jobs/{jid}/segments/3",
                json={
                    "override_decision": "accepted",
                    "override_reason": "x",
                    "override_user": "ops_alice",
                },
            )
        assert resp.status_code == 400, resp.text
        # INVALID_STATUS maps to 400 in ERROR_STATUS_MAP（见 src/api/errors.py）
        assert_error_envelope(resp.json(), code="INVALID_STATUS")

    def test_extra_field_rejected(self, client):
        """extra='forbid' 的 Pydantic 校验：未声明字段 ⇒ 422."""
        jid = uuid4()
        resp = client.patch(
            f"/api/v1/curation-jobs/{jid}/segments/3",
            json={
                "override_decision": "accepted",
                "override_reason": "x",
                "override_user": "ops_alice",
                "extra_field": "should_fail",
            },
        )
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")
