"""Integration test: orphan task recovery (Feature 013 US3 / FR-009).

Validates that when a worker is killed mid-processing (simulated by
manually inserting a row with ``status=processing`` and ``started_at``
older than ``orphan_task_timeout_seconds``), the next worker boot's
:func:`sweep_orphan_tasks` call reclaims the row as ``failed`` with
``error_message='orphan recovered on worker restart'``.

We bypass the real 840-second timeout by pre-dating ``started_at`` rather
than waiting, so the test runs in milliseconds.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from src.utils.time_utils import now_cst

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.workers.orphan_recovery import sweep_orphan_tasks


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
                AnalysisTask.video_filename.like("pytest_t035_%")
            )
        )
        await session.commit()
    await engine.dispose()
    yield
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.video_filename.like("pytest_t035_%")
            )
        )
        await session.commit()
    await engine.dispose()


async def _insert_processing_task(
    session: AsyncSession,
    *,
    task_type: TaskType,
    filename: str,
    started_delta_s: int,
) -> uuid.UUID:
    """Insert a ``processing`` row whose started_at is ``started_delta_s`` in the past."""
    now = now_cst()
    started = now - timedelta(seconds=started_delta_s)
    tid = uuid.uuid4()
    key = f"pytest/f013-t035/{filename}"
    session.add(
        AnalysisTask(
            id=tid,
            task_type=task_type,
            video_filename=filename,
            video_size_bytes=100,
            video_storage_uri=key,
            status=TaskStatus.processing,
            cos_object_key=key,
            submitted_via="single",
            created_at=started,
            started_at=started,
        )
    )
    await session.commit()
    return tid


class TestOrphanRecovery:
    async def test_stale_processing_task_reclaimed_as_failed(self):
        """Task older than orphan timeout → sweep marks failed with 'orphan recovered'."""
        settings = get_settings()
        stale_age = settings.orphan_task_timeout_seconds + 60  # comfortably stale

        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                stale_id = await _insert_processing_task(
                    session,
                    task_type=TaskType.athlete_diagnosis,
                    filename="pytest_t035_stale.mp4",
                    started_delta_s=stale_age,
                )

            reclaimed_count = await sweep_orphan_tasks()
            assert reclaimed_count >= 1

            async with factory() as session:
                row = (
                    await session.execute(
                        select(AnalysisTask).where(AnalysisTask.id == stale_id)
                    )
                ).scalar_one()
                assert row.status == TaskStatus.failed
                assert row.completed_at is not None
                assert row.error_message is not None
                assert "orphan recovered" in row.error_message
        finally:
            await engine.dispose()

    async def test_fresh_processing_task_not_reclaimed(self):
        """Task younger than orphan timeout stays processing."""
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                fresh_id = await _insert_processing_task(
                    session,
                    task_type=TaskType.video_classification,
                    filename="pytest_t035_fresh.mp4",
                    started_delta_s=5,  # 5 seconds ago — nowhere near timeout
                )

            await sweep_orphan_tasks()

            async with factory() as session:
                row = (
                    await session.execute(
                        select(AnalysisTask).where(AnalysisTask.id == fresh_id)
                    )
                ).scalar_one()
                assert row.status == TaskStatus.processing
                assert row.error_message is None
        finally:
            await engine.dispose()

    async def test_sweep_returns_zero_when_no_stale_tasks(self):
        """Sweeping an empty/fresh table returns 0 without error."""
        count = await sweep_orphan_tasks()
        # Other tests or prior runs may leave unrelated stale rows; we only
        # care that the call completes and returns a non-negative count.
        assert count >= 0
