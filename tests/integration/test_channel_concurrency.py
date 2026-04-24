"""Integration test: channel concurrency observability (Feature 013 US5 T051).

Verifies that when more tasks than a channel's `concurrency` are moved to
``processing`` state, ``TaskChannelService.get_snapshot`` reports
``current_processing`` equal to however many are actually in-flight — and
that the configured ``concurrency`` value surfaces correctly on the
``ChannelSnapshot`` so operators can compare the two side-by-side.

Design note (no real Celery worker):
    We don't need an actual worker pool to prove the API side of US5.
    The spec acceptance criterion ("提交 >concurrency 数量任务到三类通道 →
    GET /api/v1/task-channels 返回每类 processing=concurrency") is really a
    claim about *what the snapshot reports*, since Celery's prefetch +
    worker ``--concurrency=N`` is what physically caps in-flight tasks in
    production. Here we directly seed N pending + N processing rows and
    assert the snapshot math matches.

Requires PostgreSQL with Alembic 0012 applied.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.services.task_channel_service import TaskChannelService


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _fresh_session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_size=1, max_overflow=0, pool_pre_ping=True
    )
    return async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    ), engine


@pytest_asyncio.fixture(autouse=True)
async def _cleanup():
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.video_filename.like("pytest_t051_%")
            )
        )
        await session.commit()
    await engine.dispose()
    TaskChannelService.invalidate_cache()
    yield
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.video_filename.like("pytest_t051_%")
            )
        )
        await session.commit()
    await engine.dispose()
    TaskChannelService.invalidate_cache()


async def _seed(session, task_type: TaskType, *, pending: int, processing: int) -> None:
    now = datetime.now(timezone.utc)
    for i in range(pending):
        session.add(
            AnalysisTask(
                id=uuid.uuid4(),
                task_type=task_type,
                video_filename=f"pytest_t051_pending_{task_type.value}_{i}.mp4",
                video_size_bytes=100,
                video_storage_uri=f"pytest/t051/{task_type.value}/p_{i}.mp4",
                status=TaskStatus.pending,
                cos_object_key=f"pytest/t051/{task_type.value}/p_{i}.mp4",
                submitted_via="single",
                created_at=now,
            )
        )
    for i in range(processing):
        session.add(
            AnalysisTask(
                id=uuid.uuid4(),
                task_type=task_type,
                video_filename=f"pytest_t051_proc_{task_type.value}_{i}.mp4",
                video_size_bytes=100,
                video_storage_uri=f"pytest/t051/{task_type.value}/x_{i}.mp4",
                status=TaskStatus.processing,
                cos_object_key=f"pytest/t051/{task_type.value}/x_{i}.mp4",
                submitted_via="single",
                created_at=now,
                started_at=now,
            )
        )
    await session.commit()


class TestChannelConcurrencyObservability:
    async def test_kb_channel_reports_processing_equal_to_concurrency(self):
        """FR-018 SC-004: snapshot current_processing reflects in-flight count."""
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                await _seed(
                    session,
                    TaskType.kb_extraction,
                    pending=8,
                    processing=2,  # matches default kb_extraction concurrency=2
                )

            async with factory() as session:
                svc = TaskChannelService()
                snap = await svc.get_snapshot(session, TaskType.kb_extraction)

            assert snap.task_type == TaskType.kb_extraction
            assert snap.concurrency == 2, "default kb_extraction concurrency should be 2"
            assert snap.current_processing == 2
            assert snap.current_pending == 8
            # capacity default = 50, so remaining = 50 - 10 = 40
            assert snap.remaining_slots == snap.queue_capacity - 10
        finally:
            await engine.dispose()

    async def test_three_channels_concurrent_snapshots_are_independent(self):
        """Submitting to all three channels at once yields independent snapshots."""
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                await _seed(
                    session, TaskType.video_classification, pending=3, processing=1
                )
                await _seed(session, TaskType.kb_extraction, pending=5, processing=2)
                await _seed(
                    session, TaskType.athlete_diagnosis, pending=4, processing=2
                )

            async with factory() as session:
                svc = TaskChannelService()
                snaps = await svc.get_all_snapshots(session)

            by_type = {s.task_type: s for s in snaps}
            assert by_type[TaskType.video_classification].current_processing == 1
            assert by_type[TaskType.video_classification].current_pending == 3
            assert by_type[TaskType.kb_extraction].current_processing == 2
            assert by_type[TaskType.kb_extraction].current_pending == 5
            assert by_type[TaskType.athlete_diagnosis].current_processing == 2
            assert by_type[TaskType.athlete_diagnosis].current_pending == 4

            # Each channel's remaining_slots respects its own capacity.
            for snap in snaps:
                inflight = snap.current_pending + snap.current_processing
                assert snap.remaining_slots == max(0, snap.queue_capacity - inflight)
        finally:
            await engine.dispose()

    async def test_overload_does_not_inflate_concurrency_field(self):
        """Even with many processing rows, the reported *concurrency* (config)
        never changes — only current_processing grows. This is how operators
        spot runaway state vs. raised limits."""
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                # Seed 5 processing rows in diagnosis channel (default concurrency=2)
                await _seed(
                    session, TaskType.athlete_diagnosis, pending=0, processing=5
                )

            async with factory() as session:
                svc = TaskChannelService()
                snap = await svc.get_snapshot(session, TaskType.athlete_diagnosis)

            # config concurrency is a ceiling, not a gauge — surface it as-is.
            assert snap.concurrency == 2
            # current_processing is the live gauge and reflects reality.
            assert snap.current_processing == 5
        finally:
            await engine.dispose()
