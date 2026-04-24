"""Unit tests for TaskSubmissionService (Feature 013 T059).

Pure-logic path coverage (no DB):
  - Batch size limit → BatchTooLargeError
  - Empty batch → ValueError
  - ChannelDisabled error surface
  - Advisory lock key determinism
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.analysis_task import TaskType
from src.services.task_channel_service import ChannelConfigSnapshot, TaskChannelService
from src.services.task_submission_service import (
    BatchTooLargeError,
    ChannelDisabledError,
    SubmissionInputItem,
    TaskSubmissionService,
    _advisory_lock_key,
)


def _item(key: str = "pytest/unit/x.mp4") -> SubmissionInputItem:
    return SubmissionInputItem(
        cos_object_key=key,
        task_kwargs={},
        video_filename=key.rsplit("/", 1)[-1],
        video_size_bytes=100,
        video_storage_uri=key,
    )


class TestBatchSizeLimits:
    @pytest.mark.asyncio
    async def test_empty_batch_raises_valueerror(self):
        svc = TaskSubmissionService(batch_max_size=100)
        fake_session = MagicMock()
        fake_session.execute = AsyncMock()

        with pytest.raises(ValueError, match="items must not be empty"):
            await svc.submit_batch(
                fake_session, TaskType.kb_extraction, items=[]
            )

    @pytest.mark.asyncio
    async def test_batch_over_max_raises_batchtoolarge(self):
        svc = TaskSubmissionService(batch_max_size=5)
        fake_session = MagicMock()
        fake_session.execute = AsyncMock()

        items = [_item(f"pytest/unit/{i}.mp4") for i in range(6)]
        with pytest.raises(BatchTooLargeError, match="exceeds max 5"):
            await svc.submit_batch(
                fake_session, TaskType.kb_extraction, items=items
            )

    @pytest.mark.asyncio
    async def test_batch_at_exact_limit_passes_size_check(self):
        """Size-limit itself passes when len == max; subsequent logic requires DB."""
        svc = TaskSubmissionService(batch_max_size=3)

        # Trigger a ChannelDisabled to exit early after the size check but
        # before running any DB mutations.
        channel_svc = MagicMock(spec=TaskChannelService)
        channel_svc.load_config = AsyncMock(
            return_value=ChannelConfigSnapshot(
                task_type=TaskType.kb_extraction,
                queue_capacity=10, concurrency=2, enabled=False,
            )
        )
        svc = TaskSubmissionService(channel_service=channel_svc, batch_max_size=3)

        fake_session = MagicMock()
        fake_session.execute = AsyncMock()

        items = [_item(f"pytest/unit/{i}.mp4") for i in range(3)]
        with pytest.raises(ChannelDisabledError):
            await svc.submit_batch(
                fake_session, TaskType.kb_extraction, items=items
            )


class TestChannelDisabled:
    @pytest.mark.asyncio
    async def test_disabled_channel_raises(self):
        channel_svc = MagicMock(spec=TaskChannelService)
        channel_svc.load_config = AsyncMock(
            return_value=ChannelConfigSnapshot(
                task_type=TaskType.video_classification,
                queue_capacity=5, concurrency=1, enabled=False,
            )
        )
        svc = TaskSubmissionService(channel_service=channel_svc)

        fake_session = MagicMock()
        fake_session.execute = AsyncMock()

        with pytest.raises(ChannelDisabledError, match="video_classification"):
            await svc.submit_batch(
                fake_session, TaskType.video_classification,
                items=[_item()], submitted_via="single",
            )


class TestAdvisoryLockKey:
    """Lock key must be deterministic per task_type and fit in signed int64."""

    def test_same_type_produces_same_key(self):
        k1 = _advisory_lock_key(TaskType.kb_extraction)
        k2 = _advisory_lock_key(TaskType.kb_extraction)
        assert k1 == k2

    def test_different_types_produce_different_keys(self):
        keys = {
            _advisory_lock_key(TaskType.video_classification),
            _advisory_lock_key(TaskType.kb_extraction),
            _advisory_lock_key(TaskType.athlete_diagnosis),
        }
        assert len(keys) == 3

    def test_key_fits_in_signed_int64(self):
        INT64_MIN = -(2**63)
        INT64_MAX = 2**63 - 1
        for tt in TaskType:
            k = _advisory_lock_key(tt)
            assert INT64_MIN <= k <= INT64_MAX


class TestSubmissionInputItem:
    """DTO defaults make routers' job easier."""

    def test_defaults_applied(self):
        item = SubmissionInputItem(cos_object_key="x", task_kwargs={})
        assert item.video_filename == ""
        assert item.video_size_bytes == 0
        assert item.video_storage_uri is None
        assert item.force is False
        assert item.coach_id is None
