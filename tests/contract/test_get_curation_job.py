"""Feature-021 T022 — GET /api/v1/curation-jobs/{job_id} 合约测试.

覆盖 contracts/get_curation_job.md 6 条用例：
1. success 状态作业 → 200 + 完整 summary + segments
2. include_segments=false → segments 数组为空
3. 含覆盖记录的作业 → has_overrides=true
4. running 状态 → summary 字段为 null
5. job_id 不存在 → 404 NOT_FOUND
6. job_id 非 UUID → 422 VALIDATION_FAILED
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

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


_PATCH_FETCH = "src.api.routers.curation_jobs.fetch_curation_job_with_segments"


def _make_job(*, status: str = "success", has_summary: bool = True):
    """构造伪 ORM 对象（用 SimpleNamespace 模拟字段访问）。"""
    base = {
        "id": uuid4(),
        "cos_object_key": "charhuang/x/y/z.mp4",
        "coach_video_classification_id": uuid4(),
        "preprocessing_job_id": uuid4(),
        "curation_rubric_version": "v1",
        "status": status,
        "error_code": None,
        "error_message": None,
        "submitted_at": datetime(2026, 5, 18, 10, 0, 0),
        "started_at": datetime(2026, 5, 18, 10, 0, 5) if status != "pending" else None,
        "completed_at": datetime(2026, 5, 18, 10, 0, 30) if has_summary else None,
    }
    summary_fields = {
        "total_segment_count": 5 if has_summary else None,
        "accepted_segment_count": 3 if has_summary else None,
        "rejected_segment_count": 1 if has_summary else None,
        "uncertain_segment_count": 1 if has_summary else None,
        "total_duration_seconds": 50.0 if has_summary else None,
        "accepted_duration_seconds": 30.0 if has_summary else None,
        "accepted_duration_ratio": 0.6 if has_summary else None,
        "low_quality": False if has_summary else None,
        "audio_unavailable": False if has_summary else None,
        "short_video": False if has_summary else None,
    }
    return SimpleNamespace(**base, **summary_fields)


def _make_segment(idx: int, *, with_override: bool = False):
    base = {
        "segment_index": idx,
        "segment_start_ms": idx * 10000,
        "segment_end_ms": (idx + 1) * 10000,
        "auto_decision": "accepted",
        "validity_score": 0.85,
        "rejection_reason": None,
        "decision_source": "rule",
        "dim_breakdown": {"tech_keyword": {"score": 0.9, "weight": 0.35, "matched": ["示范"]}},
        "override_decision": "rejected" if with_override else None,
        "override_user": "ops_alice" if with_override else None,
        "override_reason": "误判" if with_override else None,
        "overridden_at": datetime(2026, 5, 18, 11, 20, 0) if with_override else None,
        "effective_decision": "rejected" if with_override else "accepted",
    }
    return SimpleNamespace(**base)


@pytest.mark.contract
class TestGetCurationJobContract:

    def test_c1_success_full_payload(self, client):
        job = _make_job(status="success")
        segments = [_make_segment(i) for i in range(5)]
        extras = {"has_overrides": False, "kb_stale_after_override": False}
        with patch(_PATCH_FETCH, new_callable=AsyncMock, return_value=(job, segments, extras)):
            resp = client.get(f"/api/v1/curation-jobs/{job.id}")
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["status"] == "success"
        assert data["summary"]["accepted_duration_ratio"] == 0.6
        assert data["summary"]["low_quality"] is False
        assert data["summary"]["has_overrides"] is False
        assert len(data["segments"]) == 5
        assert data["segments"][0]["effective_decision"] == "accepted"

    def test_c2_include_segments_false(self, client):
        job = _make_job(status="success")
        extras = {"has_overrides": False, "kb_stale_after_override": False}
        with patch(_PATCH_FETCH, new_callable=AsyncMock, return_value=(job, [], extras)) as mock_fetch:
            resp = client.get(f"/api/v1/curation-jobs/{job.id}?include_segments=false")
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["segments"] == []
        # 验证参数透传
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["include_segments"] is False

    def test_c3_with_overrides_marks_has_overrides(self, client):
        job = _make_job(status="success")
        segments = [_make_segment(0), _make_segment(1, with_override=True)]
        extras = {"has_overrides": True, "kb_stale_after_override": True}
        with patch(_PATCH_FETCH, new_callable=AsyncMock, return_value=(job, segments, extras)):
            resp = client.get(f"/api/v1/curation-jobs/{job.id}")
        data = assert_success_envelope(resp.json())
        assert data["summary"]["has_overrides"] is True
        assert data["summary"]["kb_stale_after_override"] is True
        assert data["segments"][1]["override_decision"] == "rejected"
        assert data["segments"][1]["effective_decision"] == "rejected"

    def test_c4_running_status_summary_is_null(self, client):
        job = _make_job(status="running", has_summary=False)
        extras = {"has_overrides": False, "kb_stale_after_override": False}
        with patch(_PATCH_FETCH, new_callable=AsyncMock, return_value=(job, [], extras)):
            resp = client.get(f"/api/v1/curation-jobs/{job.id}")
        data = assert_success_envelope(resp.json())
        assert data["status"] == "running"
        assert data["summary"]["total_segment_count"] is None
        assert data["summary"]["accepted_duration_ratio"] is None
        assert data["completed_at"] is None

    def test_c5_job_not_found(self, client):
        jid = uuid4()
        with patch(_PATCH_FETCH, new_callable=AsyncMock, return_value=None):
            resp = client.get(f"/api/v1/curation-jobs/{jid}")
        assert resp.status_code == 404, resp.text
        err = assert_error_envelope(resp.json(), code="NOT_FOUND")
        assert err["details"]["resource_id"] == str(jid)

    def test_c6_invalid_uuid_validation(self, client):
        resp = client.get("/api/v1/curation-jobs/not-a-uuid")
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")
