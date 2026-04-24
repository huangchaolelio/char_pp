"""Unit tests for TaskChannelService (Feature 013 T058).

Focuses on pure-logic paths that don't require a DB:
  - Cache TTL behaviour (hit vs. expiry vs. invalidation)
  - ChannelLiveSnapshot composition (remaining_slots math)
  - update_config validation errors
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.analysis_task import TaskType
from src.services.task_channel_service import (
    ChannelConfigSnapshot,
    ChannelLiveSnapshot,
    TaskChannelService,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    TaskChannelService.invalidate_cache()
    yield
    TaskChannelService.invalidate_cache()


class TestCacheTTL:
    """load_config respects TTL and invalidation."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db_on_second_call(self):
        svc = TaskChannelService(ttl_seconds=30)

        mock_row = MagicMock(
            task_type=TaskType.kb_extraction,
            queue_capacity=50,
            concurrency=2,
            enabled=True,
        )
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_row))
        )

        snap1 = await svc.load_config(fake_session, TaskType.kb_extraction)
        snap2 = await svc.load_config(fake_session, TaskType.kb_extraction)

        assert snap1 == snap2
        # Second call served from cache — execute only called once.
        assert fake_session.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_miss_after_ttl_expiry_reloads_from_db(self):
        svc = TaskChannelService(ttl_seconds=0)  # always expired

        mock_row = MagicMock(
            task_type=TaskType.video_classification,
            queue_capacity=5,
            concurrency=1,
            enabled=True,
        )
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_row))
        )

        await svc.load_config(fake_session, TaskType.video_classification)
        await svc.load_config(fake_session, TaskType.video_classification)

        assert fake_session.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_cache_forces_next_reload(self):
        svc = TaskChannelService(ttl_seconds=3600)

        mock_row = MagicMock(
            task_type=TaskType.athlete_diagnosis,
            queue_capacity=20,
            concurrency=2,
            enabled=True,
        )
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_row))
        )

        await svc.load_config(fake_session, TaskType.athlete_diagnosis)
        TaskChannelService.invalidate_cache(TaskType.athlete_diagnosis)
        await svc.load_config(fake_session, TaskType.athlete_diagnosis)

        assert fake_session.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_all_clears_every_channel(self):
        svc = TaskChannelService(ttl_seconds=3600)
        rows = {
            TaskType.video_classification: MagicMock(
                task_type=TaskType.video_classification,
                queue_capacity=5, concurrency=1, enabled=True,
            ),
            TaskType.kb_extraction: MagicMock(
                task_type=TaskType.kb_extraction,
                queue_capacity=50, concurrency=2, enabled=True,
            ),
        }

        fake_session = MagicMock()

        async def _exec(stmt, *a, **kw):
            # Return a different row per call based on current call count
            call_idx = fake_session.execute.await_count
            tts = list(rows.keys())
            tt = tts[(call_idx - 1) % len(tts)]
            return MagicMock(scalar_one_or_none=MagicMock(return_value=rows[tt]))

        fake_session.execute = AsyncMock(side_effect=_exec)

        await svc.load_config(fake_session, TaskType.video_classification)
        await svc.load_config(fake_session, TaskType.kb_extraction)
        TaskChannelService.invalidate_cache()  # all
        await svc.load_config(fake_session, TaskType.video_classification)

        assert fake_session.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_missing_config_row_raises_valueerror(self):
        svc = TaskChannelService(ttl_seconds=30)
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        with pytest.raises(ValueError, match="no task_channel_configs"):
            await svc.load_config(fake_session, TaskType.kb_extraction)


class TestUpdateConfigValidation:
    """update_config enforces positive-integer bounds."""

    @pytest.mark.asyncio
    async def test_queue_capacity_zero_rejected(self):
        svc = TaskChannelService()
        mock_row = MagicMock(
            task_type=TaskType.kb_extraction,
            queue_capacity=50, concurrency=2, enabled=True,
        )
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_row))
        )
        fake_session.commit = AsyncMock()

        with pytest.raises(ValueError, match="queue_capacity"):
            await svc.update_config(
                fake_session, TaskType.kb_extraction, queue_capacity=0
            )

    @pytest.mark.asyncio
    async def test_concurrency_negative_rejected(self):
        svc = TaskChannelService()
        mock_row = MagicMock(
            task_type=TaskType.kb_extraction,
            queue_capacity=50, concurrency=2, enabled=True,
        )
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_row))
        )
        fake_session.commit = AsyncMock()

        with pytest.raises(ValueError, match="concurrency"):
            await svc.update_config(
                fake_session, TaskType.kb_extraction, concurrency=-1
            )

    @pytest.mark.asyncio
    async def test_missing_row_raises(self):
        svc = TaskChannelService()
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        with pytest.raises(ValueError, match="no task_channel_configs"):
            await svc.update_config(
                fake_session, TaskType.kb_extraction, queue_capacity=10
            )

    @pytest.mark.asyncio
    async def test_successful_update_commits_and_invalidates(self):
        """Happy path: patch applied, cache cleared for this channel."""
        svc = TaskChannelService(ttl_seconds=3600)

        # Prime cache for this channel.
        TaskChannelService._cache[TaskType.kb_extraction] = (
            time.monotonic(),
            ChannelConfigSnapshot(
                task_type=TaskType.kb_extraction,
                queue_capacity=50, concurrency=2, enabled=True,
            ),
        )

        mock_row = MagicMock(
            task_type=TaskType.kb_extraction,
            queue_capacity=50, concurrency=2, enabled=True,
        )
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_row))
        )
        fake_session.commit = AsyncMock()

        result = await svc.update_config(
            fake_session, TaskType.kb_extraction,
            queue_capacity=100, concurrency=4, enabled=False,
        )

        assert result.queue_capacity == 100
        assert result.concurrency == 4
        assert result.enabled is False
        assert mock_row.queue_capacity == 100
        assert mock_row.concurrency == 4
        assert mock_row.enabled is False
        fake_session.commit.assert_awaited_once()
        # cache invalidated for this task_type
        assert TaskType.kb_extraction not in TaskChannelService._cache


class TestSnapshotMath:
    """ChannelLiveSnapshot.remaining_slots matches queue_capacity - inflight."""

    def test_remaining_slots_never_negative(self):
        snap = ChannelLiveSnapshot(
            task_type=TaskType.kb_extraction,
            queue_capacity=50,
            concurrency=2,
            current_pending=40,
            current_processing=15,  # overbooked by 5
            remaining_slots=max(0, 50 - (40 + 15)),
            enabled=True,
            recent_completion_rate_per_min=0.0,
        )
        assert snap.remaining_slots == 0

    def test_remaining_slots_standard_case(self):
        snap = ChannelLiveSnapshot(
            task_type=TaskType.video_classification,
            queue_capacity=5,
            concurrency=1,
            current_pending=2,
            current_processing=1,
            remaining_slots=5 - 3,
            enabled=True,
            recent_completion_rate_per_min=1.5,
        )
        assert snap.remaining_slots == 2
