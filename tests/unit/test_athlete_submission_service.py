"""Unit tests for AthleteSubmissionService batch behaviors (T038)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.api.errors import AppException, ErrorCode
from src.services.athlete_submission_service import (
    submit_athlete_diagnosis_batch,
)


@pytest.mark.unit
@pytest.mark.asyncio
class TestAthleteSubmissionServiceBatchDiagnosis:

    async def test_batch_with_one_not_preprocessed_rest_ok(self):
        """3 条中 1 条未预处理 → rejected[0] 精确含 ATHLETE_VIDEO_NOT_PREPROCESSED；其余 2 条正常."""
        db = AsyncMock()
        # 通道容量预检放行
        from src.services.task_channel_service import TaskChannelService

        # patch TaskChannelService.load_config 返回开启/容量够
        cfg_mock = type("Cfg", (), {"enabled": True, "queue_capacity": 100})()

        async def _fake_load(self, session, task_type):
            return cfg_mock

        # 模拟 inflight count = 0（execute 链式 scalar_one）
        execute_result = AsyncMock()
        execute_result.scalar_one = lambda: 0
        db.execute = AsyncMock(return_value=execute_result)

        ok1, ok2, bad = uuid4(), uuid4(), uuid4()

        # 3 次 submit_athlete_diagnosis 单条的行为：第 3 次抛 NOT_PREPROCESSED
        from src.services import athlete_submission_service as svc

        async def _fake_single(db, *, classification_id, force):
            if classification_id == bad:
                raise AppException(
                    ErrorCode.ATHLETE_VIDEO_NOT_PREPROCESSED,
                    details={"athlete_video_classification_id": str(bad)},
                )
            return svc.AthleteDiagnosisOutcome(
                task_id=uuid4(),
                athlete_video_classification_id=classification_id,
                tech_category="forehand_attack",
                status="pending",
            )

        with patch.object(
            TaskChannelService, "load_config", new=_fake_load,
        ), patch.object(
            svc, "submit_athlete_diagnosis", side_effect=_fake_single,
        ):
            out = await submit_athlete_diagnosis_batch(
                db,
                items=[(ok1, False), (ok2, False), (bad, False)],
            )

        assert len(out.submitted) == 2
        assert {s.athlete_video_classification_id for s in out.submitted} == {ok1, ok2}
        assert len(out.rejected) == 1
        assert out.rejected[0].athlete_video_classification_id == bad
        assert out.rejected[0].error_code == "ATHLETE_VIDEO_NOT_PREPROCESSED"

    async def test_batch_channel_full_atomic_rejection(self):
        """通道剩余 < 请求数 → 整批 CHANNEL_QUEUE_FULL 抛出；不进入逐条循环."""
        from src.services.task_channel_service import TaskChannelService

        db = AsyncMock()
        cfg_mock = type("Cfg", (), {"enabled": True, "queue_capacity": 3})()

        async def _fake_load(self, session, task_type):
            return cfg_mock

        # inflight=3 → remaining=0 < 5 items
        execute_result = AsyncMock()
        execute_result.scalar_one = lambda: 3
        db.execute = AsyncMock(return_value=execute_result)

        ids = [uuid4() for _ in range(5)]

        with patch.object(TaskChannelService, "load_config", new=_fake_load):
            with pytest.raises(AppException) as exc:
                await submit_athlete_diagnosis_batch(
                    db, items=[(i, False) for i in ids],
                )

        assert exc.value.code == ErrorCode.CHANNEL_QUEUE_FULL
