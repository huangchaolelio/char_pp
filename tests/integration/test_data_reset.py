"""Integration test: TaskResetService against a real PostgreSQL (Feature 013 US4).

Verifies the spec's "preserved vs deleted" contract end-to-end:
  * Insert sample rows into ``analysis_tasks`` and ``coach_video_classifications``.
  * Call ``TaskResetService.reset(dry_run=False)`` → ``analysis_tasks`` is
    empty, ``coach_video_classifications`` row count unchanged.
  * dry_run=True → counts returned but mutations skipped.

Uses pytest_t044 prefixes on test-owned rows so production data (if any)
is untouched by the cleanup fixture; the actual reset, however, truncates
*all* rows from the target tables — tests must run on a dedicated test DB.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from src.utils.time_utils import now_cst

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.services.task_reset_service import TaskResetService


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
async def _cleanup_prefixed():
    """Clean rows owned by these tests; does NOT run the reset service."""
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.video_filename.like("pytest_t044_%")
            )
        )
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key.like("pytest/f013-t044/%")
            )
        )
        await session.commit()
    await engine.dispose()
    yield
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.video_filename.like("pytest_t044_%")
            )
        )
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key.like("pytest/f013-t044/%")
            )
        )
        await session.commit()
    await engine.dispose()


async def _seed_task(session: AsyncSession, filename: str) -> uuid.UUID:
    tid = uuid.uuid4()
    key = f"pytest/f013-t044/{filename}"
    session.add(
        AnalysisTask(
            id=tid,
            task_type=TaskType.video_classification,
            video_filename=filename,
            video_size_bytes=100,
            video_storage_uri=key,
            status=TaskStatus.pending,
            cos_object_key=key,
            submitted_via="single",
            created_at=now_cst(),
        )
    )
    await session.commit()
    return tid


async def _seed_classification(session: AsyncSession, key: str) -> uuid.UUID:
    cid = uuid.uuid4()
    session.add(
        CoachVideoClassification(
            id=cid,
            cos_object_key=key,
            filename=key.rsplit("/", 1)[-1],
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
    return cid


class TestDataResetIntegration:
    async def test_dry_run_reports_counts_without_deleting(self):
        """dry_run=True returns deleted_counts >=0 but rows still exist."""
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                await _seed_task(session, "pytest_t044_dry.mp4")
                await _seed_classification(
                    session, "pytest/f013-t044/coach/dry.mp4"
                )

            async with factory() as session:
                svc = TaskResetService()
                report = await svc.reset(session=session, dry_run=True)

            assert report.dry_run is True
            assert report.deleted_counts["analysis_tasks"] >= 1
            assert report.preserved_counts["coach_video_classifications"] >= 1

            # Verify nothing actually deleted.
            async with factory() as session:
                task_cnt = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(AnalysisTask).where(
                                AnalysisTask.video_filename
                                == "pytest_t044_dry.mp4"
                            )
                        )
                    ).scalar_one()
                )
                cls_cnt = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(CoachVideoClassification)
                            .where(
                                CoachVideoClassification.cos_object_key
                                == "pytest/f013-t044/coach/dry.mp4"
                            )
                        )
                    ).scalar_one()
                )
                assert task_cnt == 1
                assert cls_cnt == 1
        finally:
            await engine.dispose()

    async def test_real_reset_truncates_tasks_preserves_classifications(self):
        """Non-dry run actually truncates analysis_tasks, leaves classifications."""
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                await _seed_task(session, "pytest_t044_real_a.mp4")
                await _seed_task(session, "pytest_t044_real_b.mp4")
                await _seed_classification(
                    session, "pytest/f013-t044/coach/real_a.mp4"
                )
                await _seed_classification(
                    session, "pytest/f013-t044/coach/real_b.mp4"
                )

            # Capture preserved counts before reset.
            async with factory() as session:
                pre_cls_cnt = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(CoachVideoClassification)
                        )
                    ).scalar_one()
                )

            async with factory() as session:
                svc = TaskResetService()
                report = await svc.reset(session=session, dry_run=False)

            assert report.dry_run is False
            assert report.deleted_counts["analysis_tasks"] >= 2

            async with factory() as session:
                # analysis_tasks must be empty (TRUNCATE).
                task_cnt = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(AnalysisTask)
                        )
                    ).scalar_one()
                )
                assert task_cnt == 0

                # coach_video_classifications count unchanged.
                post_cls_cnt = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(CoachVideoClassification)
                        )
                    ).scalar_one()
                )
                assert post_cls_cnt == pre_cls_cnt
        finally:
            await engine.dispose()

    async def test_reset_preserves_task_channel_configs(self):
        """Channel configs are operational data — must survive a reset."""
        factory, engine = _fresh_session_factory()
        try:
            async with factory() as session:
                pre_cfg_cnt = int(
                    (
                        await session.execute(
                            text("SELECT COUNT(*) FROM task_channel_configs")
                        )
                    ).scalar_one()
                )

            async with factory() as session:
                svc = TaskResetService()
                await svc.reset(session=session, dry_run=False)

            async with factory() as session:
                post_cfg_cnt = int(
                    (
                        await session.execute(
                            text("SELECT COUNT(*) FROM task_channel_configs")
                        )
                    ).scalar_one()
                )
            assert post_cfg_cnt == pre_cfg_cnt
        finally:
            await engine.dispose()
