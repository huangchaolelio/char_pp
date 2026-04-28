"""Integration test: batch throttling and oversize rejection (Feature 013 US2).

FR-008 / SC-001 verifications:
  * Classification channel (capacity=5): pre-fill 3 rows; submit a 5-item
    batch → response has ``accepted=2, rejected=3`` with QUEUE_FULL codes on
    the overflow items.
  * Submitting 101 items in one call → whole batch rejected with HTTP 400
    ``BATCH_TOO_LARGE`` (service raises ``BatchTooLargeError`` before any row
    is inserted).

Each test spins up its own async engine to avoid asyncpg/event-loop teardown
races under pytest-asyncio (see ``test_task_pipeline_isolation.py``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from src.utils.time_utils import now_cst

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.services.task_submission_service import (
    BatchTooLargeError,
    SubmissionInputItem,
    TaskSubmissionService,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _fresh_session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_size=1, max_overflow=0, pool_pre_ping=True
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    return factory, engine


@pytest.fixture(autouse=True)
async def _cleanup():
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.cos_object_key.like("pytest/f013-t028/%")
            )
        )
        await session.commit()
    await engine.dispose()
    yield
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.cos_object_key.like("pytest/f013-t028/%")
            )
        )
        await session.commit()
    await engine.dispose()


async def _prefill_classification(session: AsyncSession, n: int) -> None:
    now = now_cst()
    for i in range(n):
        key = f"pytest/f013-t028/prefill_{i}.mp4"
        session.add(
            AnalysisTask(
                id=uuid.uuid4(),
                task_type=TaskType.video_classification,
                video_filename=f"prefill_{i}.mp4",
                video_size_bytes=100,
                video_storage_uri=key,
                status=TaskStatus.pending,
                cos_object_key=key,
                submitted_via="single",
                created_at=now,
            )
        )
    await session.commit()


class TestBatchThrottling:
    async def test_partial_success_when_channel_partially_full(self):
        """3 pending pre-filled; batch of 5 → accepted=2, rejected=3 (QUEUE_FULL)."""
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                await _prefill_classification(session, n=3)

            async with factory() as session:
                svc = TaskSubmissionService()
                items = [
                    SubmissionInputItem(
                        cos_object_key=f"pytest/f013-t028/batch_{i}.mp4",
                        task_kwargs={},
                        video_filename=f"batch_{i}.mp4",
                        video_storage_uri=f"pytest/f013-t028/batch_{i}.mp4",
                    )
                    for i in range(5)
                ]
                result = await svc.submit_batch(
                    session=session,
                    task_type=TaskType.video_classification,
                    items=items,
                    submitted_via="batch",
                )

            assert result.accepted == 2
            assert result.rejected == 3
            # First 2 accepted, last 3 rejected QUEUE_FULL in input order.
            assert [o.accepted for o in result.items] == [True, True, False, False, False]
            assert all(
                o.rejection_code == "QUEUE_FULL"
                for o in result.items
                if not o.accepted
            )

            # Verify only 2 new rows landed (5 total = 3 prefill + 2 accepted).
            async with factory() as session:
                count = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(AnalysisTask).where(
                                AnalysisTask.cos_object_key.like(
                                    "pytest/f013-t028/%"
                                )
                            )
                        )
                    ).scalar_one()
                )
                assert count == 5
        finally:
            await engine.dispose()

    async def test_oversize_batch_raises_batch_too_large_error(self):
        """101 items → BatchTooLargeError raised; no rows inserted."""
        settings = get_settings()
        oversize = settings.batch_max_size + 1

        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                svc = TaskSubmissionService()
                items = [
                    SubmissionInputItem(
                        cos_object_key=f"pytest/f013-t028/oversize_{i}.mp4",
                        task_kwargs={},
                        video_filename=f"oversize_{i}.mp4",
                        video_storage_uri=f"pytest/f013-t028/oversize_{i}.mp4",
                    )
                    for i in range(oversize)
                ]
                with pytest.raises(BatchTooLargeError):
                    await svc.submit_batch(
                        session=session,
                        task_type=TaskType.video_classification,
                        items=items,
                        submitted_via="batch",
                    )

            async with factory() as session:
                count = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(AnalysisTask).where(
                                AnalysisTask.cos_object_key.like(
                                    "pytest/f013-t028/oversize_%"
                                )
                            )
                        )
                    ).scalar_one()
                )
                assert count == 0
        finally:
            await engine.dispose()

    async def test_exactly_max_size_batch_accepted(self):
        """Boundary: batch_max_size items is allowed (only capacity may reject)."""
        settings = get_settings()

        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                svc = TaskSubmissionService()
                items = [
                    SubmissionInputItem(
                        cos_object_key=f"pytest/f013-t028/max_{i}.mp4",
                        task_kwargs={},
                        video_filename=f"max_{i}.mp4",
                        video_storage_uri=f"pytest/f013-t028/max_{i}.mp4",
                    )
                    for i in range(settings.batch_max_size)
                ]
                result = await svc.submit_batch(
                    session=session,
                    task_type=TaskType.video_classification,
                    items=items,
                    submitted_via="batch",
                )
            # Classification channel capacity default is 5, so at most 5 accepted.
            assert result.accepted + result.rejected == settings.batch_max_size
            assert result.accepted <= result.channel.queue_capacity
            # All overflow → QUEUE_FULL.
            overflow = [o for o in result.items if not o.accepted]
            assert all(o.rejection_code == "QUEUE_FULL" for o in overflow)
        finally:
            await engine.dispose()
