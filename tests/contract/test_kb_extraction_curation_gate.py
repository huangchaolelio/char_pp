"""Feature-021 T050 — POST /api/v1/tasks/kb-extraction 清洗门合约测试.

覆盖 contracts/kb_extraction_curation_gate.md 7 条用例：

1. 视频无 success 清洗记录 ⇒ 409 CURATION_REQUIRED（单条）
2. 视频清洗 accepted_duration_ratio==0 ⇒ 提交成功；
   后续 DAG 在 download_video 抛 LOW_QUALITY_SKIP（合约层仅断言提交成功 +
   gate 接收到 low_quality_skip 决策传递给后续）
3. 视频清洗 accepted_duration_ratio∈(0, 0.3) ⇒ 200 + 后续 warning（DAG 内）
4. 视频清洗 accepted_duration_ratio≥0.3 ⇒ 200 + 无 warning
5. download_video 中 segments_processed = accepted（在 T051 集成测试中验证；
   此合约层仅断言路由不抛 CURATION_REQUIRED）
6. bypass=true ⇒ 路由放行（即使没有 success 清洗）
7. bypass 关闭后再提交未清洗视频 ⇒ 409 CURATION_REQUIRED（开关回滚后语义恢复）

批量端点的并行场景由 ``test_kb_extraction_batch_curation_gate`` 覆盖。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.errors import ErrorCode
from src.api.main import app
from src.services.curation.kb_gate import GateResult
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


def _ok_submission_result():
    """构造一个 F-013 SubmissionBatchResult 让 _f13_submit 直通."""
    from src.models.analysis_task import TaskType
    from src.services.task_channel_service import ChannelLiveSnapshot
    from src.services.task_submission_service import (
        SubmissionBatchResult,
        SubmissionOutcome,
    )

    snap = ChannelLiveSnapshot(
        task_type=TaskType.kb_extraction,
        queue_capacity=50, concurrency=2,
        current_pending=1, current_processing=0,
        remaining_slots=49, enabled=True,
        recent_completion_rate_per_min=0.0,
    )
    return SubmissionBatchResult(
        task_type=TaskType.kb_extraction,
        accepted=1, rejected=0,
        items=[SubmissionOutcome(
            index=0, accepted=True, task_id=uuid4(),
            cos_object_key="charhuang/x/y.mp4",
        )],
        channel=snap,
        submitted_at=datetime.now(),
    )


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


def _patch_classification_gate_pass():
    """让 F-013 分类门通过（tech_category=forehand_topspin），便于专注测 F-021."""
    return patch(
        "src.api.routers.tasks._F13ClassificationGateService"
    )


@pytest.mark.contract
class TestKbExtractionCurationGateSingle:

    def test_c1_no_curation_record_returns_curation_required(self, client):
        with (
            _patch_classification_gate_pass() as GateCls,
            patch(
                "src.api.routers.tasks.evaluate_curation_gate",
                new_callable=AsyncMock,
                return_value=GateResult(decision="required"),
            ),
        ):
            GateCls.return_value.check_classified = AsyncMock(return_value=True)
            GateCls.return_value.get_tech_category = AsyncMock(
                return_value="forehand_topspin"
            )
            resp = client.post(
                "/api/v1/tasks/kb-extraction",
                json={"cos_object_key": "charhuang/x/y.mp4"},
            )
        assert resp.status_code == 409, resp.text
        err = assert_error_envelope(resp.json(), code="CURATION_REQUIRED")
        assert err["details"]["cos_object_key"] == "charhuang/x/y.mp4"
        assert "submit POST" in err["details"]["hint"]

    def test_c2_low_quality_skip_passes_router_layer(self, client):
        """accepted_duration_ratio==0 时 router 仍放行；
        实际短路在 download_video DAG 层（T052 覆盖）。"""
        cur_jid = uuid4()
        with (
            _patch_classification_gate_pass() as GateCls,
            patch(
                "src.api.routers.tasks.evaluate_curation_gate",
                new_callable=AsyncMock,
                return_value=GateResult(
                    decision="low_quality_skip",
                    curation_job_id=cur_jid,
                    curation_rubric_version="v1",
                    accepted_duration_ratio=0.0,
                ),
            ),
            patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls,
        ):
            GateCls.return_value.check_classified = AsyncMock(return_value=True)
            GateCls.return_value.get_tech_category = AsyncMock(
                return_value="forehand_topspin"
            )
            SvcCls.return_value.submit_batch = AsyncMock(return_value=_ok_submission_result())
            resp = client.post(
                "/api/v1/tasks/kb-extraction",
                json={"cos_object_key": "charhuang/x/y.mp4"},
            )
        # 路由层不拦截 low_quality_skip——交给 DAG 层在 download_video 决定
        assert resp.status_code == 200, resp.text
        assert_success_envelope(resp.json())

    def test_c3_low_quality_warn_passes(self, client):
        cur_jid = uuid4()
        with (
            _patch_classification_gate_pass() as GateCls,
            patch(
                "src.api.routers.tasks.evaluate_curation_gate",
                new_callable=AsyncMock,
                return_value=GateResult(
                    decision="low_quality_warn",
                    curation_job_id=cur_jid,
                    curation_rubric_version="v1",
                    accepted_duration_ratio=0.2,
                ),
            ),
            patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls,
        ):
            GateCls.return_value.check_classified = AsyncMock(return_value=True)
            GateCls.return_value.get_tech_category = AsyncMock(
                return_value="forehand_topspin"
            )
            SvcCls.return_value.submit_batch = AsyncMock(return_value=_ok_submission_result())
            resp = client.post(
                "/api/v1/tasks/kb-extraction",
                json={"cos_object_key": "charhuang/x/y.mp4"},
            )
        assert resp.status_code == 200, resp.text

    def test_c4_normal_curation_passes(self, client):
        cur_jid = uuid4()
        with (
            _patch_classification_gate_pass() as GateCls,
            patch(
                "src.api.routers.tasks.evaluate_curation_gate",
                new_callable=AsyncMock,
                return_value=GateResult(
                    decision="ok",
                    curation_job_id=cur_jid,
                    curation_rubric_version="v1",
                    accepted_duration_ratio=0.7,
                ),
            ),
            patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls,
        ):
            GateCls.return_value.check_classified = AsyncMock(return_value=True)
            GateCls.return_value.get_tech_category = AsyncMock(
                return_value="forehand_topspin"
            )
            SvcCls.return_value.submit_batch = AsyncMock(return_value=_ok_submission_result())
            resp = client.post(
                "/api/v1/tasks/kb-extraction",
                json={"cos_object_key": "charhuang/x/y.mp4"},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["accepted"] == 1

    def test_c6_bypass_lets_uncurated_video_through(self, client):
        with (
            _patch_classification_gate_pass() as GateCls,
            patch(
                "src.api.routers.tasks.evaluate_curation_gate",
                new_callable=AsyncMock,
                return_value=GateResult(decision="bypassed"),
            ),
            patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls,
        ):
            GateCls.return_value.check_classified = AsyncMock(return_value=True)
            GateCls.return_value.get_tech_category = AsyncMock(
                return_value="forehand_topspin"
            )
            SvcCls.return_value.submit_batch = AsyncMock(return_value=_ok_submission_result())
            resp = client.post(
                "/api/v1/tasks/kb-extraction",
                json={"cos_object_key": "charhuang/x/y.mp4"},
            )
        assert resp.status_code == 200, resp.text

    def test_c7_bypass_off_returns_required(self, client):
        """开关关闭后语义恢复（重新提交未清洗视频被拒绝）."""
        with (
            _patch_classification_gate_pass() as GateCls,
            patch(
                "src.api.routers.tasks.evaluate_curation_gate",
                new_callable=AsyncMock,
                return_value=GateResult(decision="required"),
            ),
        ):
            GateCls.return_value.check_classified = AsyncMock(return_value=True)
            GateCls.return_value.get_tech_category = AsyncMock(
                return_value="forehand_topspin"
            )
            resp = client.post(
                "/api/v1/tasks/kb-extraction",
                json={"cos_object_key": "charhuang/x/y.mp4"},
            )
        assert resp.status_code == 409, resp.text
        assert_error_envelope(resp.json(), code="CURATION_REQUIRED")


@pytest.mark.contract
class TestKbExtractionCurationGateBatch:

    def test_batch_mixed_curation_gate(self, client):
        """3 条混合：1 通过 / 1 CURATION_REQUIRED / 1 通过.
        断言被拒绝条目落在 rejected[]，rejection_code='CURATION_REQUIRED'."""
        cur_jid = uuid4()
        ok_gate = GateResult(
            decision="ok", curation_job_id=cur_jid,
            curation_rubric_version="v1", accepted_duration_ratio=0.7,
        )
        required_gate = GateResult(decision="required")
        gate_calls = [ok_gate, required_gate, ok_gate]

        async def _gate_side_effect(session, *, cos_object_key):
            return gate_calls.pop(0)

        # 复制 _ok_submission_result 但改成 2 条（前后两条通过）
        from src.models.analysis_task import TaskType
        from src.services.task_channel_service import ChannelLiveSnapshot
        from src.services.task_submission_service import (
            SubmissionBatchResult,
            SubmissionOutcome,
        )

        snap = ChannelLiveSnapshot(
            task_type=TaskType.kb_extraction,
            queue_capacity=50, concurrency=2,
            current_pending=2, current_processing=0,
            remaining_slots=48, enabled=True,
            recent_completion_rate_per_min=0.0,
        )
        ok_result = SubmissionBatchResult(
            task_type=TaskType.kb_extraction,
            accepted=2, rejected=0,
            items=[
                SubmissionOutcome(
                    index=0, accepted=True, task_id=uuid4(),
                    cos_object_key="charhuang/a.mp4",
                ),
                SubmissionOutcome(
                    index=1, accepted=True, task_id=uuid4(),
                    cos_object_key="charhuang/c.mp4",
                ),
            ],
            channel=snap,
            submitted_at=datetime.now(),
        )

        with (
            _patch_classification_gate_pass() as GateCls,
            patch(
                "src.api.routers.tasks.evaluate_curation_gate",
                new_callable=AsyncMock,
                side_effect=_gate_side_effect,
            ),
            patch("src.api.routers.tasks._F13TaskSubmissionService") as SvcCls,
        ):
            GateCls.return_value.check_classified = AsyncMock(return_value=True)
            GateCls.return_value.get_tech_category = AsyncMock(
                return_value="forehand_topspin"
            )
            SvcCls.return_value.submit_batch = AsyncMock(return_value=ok_result)

            resp = client.post(
                "/api/v1/tasks/kb-extraction/batch",
                json={"items": [
                    {"cos_object_key": "charhuang/a.mp4"},
                    {"cos_object_key": "charhuang/b.mp4"},
                    {"cos_object_key": "charhuang/c.mp4"},
                ]},
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        # 3 items 总计；其中 1 项 CURATION_REQUIRED
        assert len(data["items"]) == 3
        rejected = [it for it in data["items"] if not it["accepted"]]
        assert len(rejected) == 1
        assert rejected[0]["rejection_code"] == "CURATION_REQUIRED"
        assert rejected[0]["cos_object_key"] == "charhuang/b.mp4"
