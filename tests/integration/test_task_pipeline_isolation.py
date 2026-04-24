"""Integration test: task pipeline channel isolation (Feature 013 US1).

Verifies that when the classification channel is saturated (5 pending rows),
a newly-submitted kb_extraction or diagnosis task still enters ``pending``
state immediately (within the service-level latency target, not blocked by
classification backpressure).

Requires:
  - PostgreSQL with Feature-013 migrations applied (0012+).
  - Redis reachable via settings.redis_url (for Celery broker — task is
    enqueued but not executed; we don't need a running worker).

The test populates the classification channel directly in the DB (bypassing
the submission service) so we don't have to wait for real Celery tasks, then
exercises the API.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.services.task_submission_service import (
    SubmissionInputItem,
    TaskSubmissionService,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _fresh_session_factory():
    """One engine per test — avoids 'Event loop is closed' on asyncpg connection
    teardown when pytest-asyncio creates a new loop for each coroutine test."""
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_size=1, max_overflow=0, pool_pre_ping=True
    )
    return async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    ), engine


@pytest_asyncio.fixture(autouse=True)
async def _cleanup():
    """Clear analysis_tasks and related classification rows around each test."""
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(delete(AnalysisTask))
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key.like("pytest/f013/%")
            )
        )
        await session.commit()
    await engine.dispose()
    yield
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(delete(AnalysisTask))
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key.like("pytest/f013/%")
            )
        )
        await session.commit()
    await engine.dispose()


async def _saturate_classification_channel(session, n: int = 5) -> None:
    """Insert n pending classification rows directly to fill capacity=5."""
    now = datetime.now(timezone.utc)
    for i in range(n):
        key = f"pytest/f013/coach/saturate_{i}.mp4"
        session.add(
            AnalysisTask(
                id=uuid.uuid4(),
                task_type=TaskType.video_classification,
                video_filename=f"saturate_{i}.mp4",
                video_size_bytes=100,
                video_storage_uri=key,
                status=TaskStatus.pending,
                cos_object_key=key,
                submitted_via="single",
                created_at=now,
            )
        )
    await session.commit()


async def _seed_classified_video(session, cos_object_key: str) -> None:
    """Insert a coach_video_classifications row so kb-extraction passes the gate."""
    session.add(
        CoachVideoClassification(
            id=uuid.uuid4(),
            cos_object_key=cos_object_key,
            filename=cos_object_key.rsplit("/", 1)[-1],
            tech_category="forehand_loop_fast",
            coach_name="pytest_coach",
            course_series="pytest",
            classification_source="manual",
            confidence=1.0,
            name_source="map",
            kb_extracted=False,
        )
    )
    await session.commit()


class TestChannelIsolation:
    async def test_kb_extraction_not_blocked_by_full_classification_channel(self):
        """FR-003 / SC-001: kb_extraction enters pending ≤5s even when classification is saturated."""
        kb_key = "pytest/f013/coach/loop_clip.mp4"
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                await _saturate_classification_channel(session, n=5)
                await _seed_classified_video(session, kb_key)

            async with factory() as session:
                svc = TaskSubmissionService()
                start = time.monotonic()
                result = await svc.submit_batch(
                    session=session,
                    task_type=TaskType.kb_extraction,
                    items=[
                        SubmissionInputItem(
                            cos_object_key=kb_key,
                            task_kwargs={"enable_audio_analysis": False, "audio_language": "zh"},
                            video_filename="loop_clip.mp4",
                            video_storage_uri=kb_key,
                        )
                    ],
                    submitted_via="single",
                )
                elapsed = time.monotonic() - start

            assert result.accepted == 1
            assert result.rejected == 0
            assert elapsed < 5.0, f"SC-001 violated: took {elapsed:.2f}s (>5s)"

            # Verify row landed in DB, pending status.
            async with factory() as session:
                from sqlalchemy import select
                row = (
                    await session.execute(
                        select(AnalysisTask).where(AnalysisTask.cos_object_key == kb_key)
                    )
                ).scalar_one()
                assert row.task_type == TaskType.kb_extraction
                assert row.status == TaskStatus.pending
        finally:
            await engine.dispose()

    async def test_classification_channel_full_rejects_new_submission(self):
        """When classification has 5/5, a 6th single submission → rejected QUEUE_FULL."""
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                await _saturate_classification_channel(session, n=5)

            async with factory() as session:
                svc = TaskSubmissionService()
                key = "pytest/f013/coach/sixth.mp4"
                result = await svc.submit_batch(
                    session=session,
                    task_type=TaskType.video_classification,
                    items=[
                        SubmissionInputItem(
                            cos_object_key=key,
                            task_kwargs={},
                            video_filename="sixth.mp4",
                            video_storage_uri=key,
                        )
                    ],
                    submitted_via="single",
                )
            assert result.accepted == 0
            assert result.rejected == 1
            assert result.items[0].rejection_code == "QUEUE_FULL"
        finally:
            await engine.dispose()

    async def test_duplicate_submission_returns_existing_task(self):
        """Idempotency (FR-008 / clarification Q5): same (cos_object_key, task_type) while pending → DUPLICATE_TASK."""
        key = "pytest/f013/coach/dedup.mp4"
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                svc = TaskSubmissionService()
                first = await svc.submit_batch(
                    session=session,
                    task_type=TaskType.video_classification,
                    items=[
                        SubmissionInputItem(
                            cos_object_key=key, task_kwargs={},
                            video_filename="dedup.mp4", video_storage_uri=key,
                        )
                    ],
                    submitted_via="single",
                )
                assert first.accepted == 1
                first_id = first.items[0].task_id

            async with factory() as session:
                svc = TaskSubmissionService()
                second = await svc.submit_batch(
                    session=session,
                    task_type=TaskType.video_classification,
                    items=[
                        SubmissionInputItem(
                            cos_object_key=key, task_kwargs={},
                            video_filename="dedup.mp4", video_storage_uri=key,
                        )
                    ],
                    submitted_via="single",
                )
            assert second.accepted == 0
            assert second.rejected == 1
            assert second.items[0].rejection_code == "DUPLICATE_TASK"
            assert second.items[0].existing_task_id == first_id
        finally:
            await engine.dispose()
