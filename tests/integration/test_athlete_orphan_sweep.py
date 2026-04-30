"""Feature-020 · T065 · 运动员任务 orphan sweep 回收测试 (FR-015).

验证 ``sweep_orphan_jobs`` Beat 任务能正确回收 3 类卡住的运动员任务：
  - ``athlete_video_classification`` (running >= TTL)
  - ``athlete_video_preprocessing`` (running >= TTL)
  - ``athlete_diagnosis``            (running >= TTL)

以及 ``video_preprocessing_jobs`` 的 running 行被同步回收（Feature-016
的级联能力在运动员预处理同模型下自然复用）。

**与 tasks.md T065 的描述差异**（已与实际实现对齐）:
  - 实际触发条件使用 ``started_at`` 而非 ``updated_at``（见
    ``src/workers/orphan_recovery.py::sweep_orphan_tasks``）
  - 实际筛选 ``status == TaskStatus.processing``（枚举值 'processing'），
    而非 "running" 字符串；本测试按实际枚举填入
  - 实际 ``error_message`` 固定为 ``"orphan recovered on worker restart"``，
    tasks.md 里的 ``ORPHAN_RECLAIMED`` 是历史描述，不是实际字符串
  - ``VideoPreprocessingJob`` 的 error_message 则是 ``"orphan_recovered"``
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.db import session as session_module
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.video_preprocessing_job import VideoPreprocessingJob
from src.utils.time_utils import now_cst


_TAG = f"__t065_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=False,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    orig_engine = session_module.engine
    orig_factory = session_module.AsyncSessionFactory
    session_module.engine = engine
    session_module.AsyncSessionFactory = factory
    try:
        yield factory
    finally:
        session_module.engine = orig_engine
        session_module.AsyncSessionFactory = orig_factory
        await engine.dispose()


async def _cleanup(factory):
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(AnalysisTask.video_filename == f"{_TAG}.mp4")
        )
        await session.execute(
            delete(VideoPreprocessingJob).where(
                VideoPreprocessingJob.cos_object_key.like(f"%{_TAG}%")
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_orphan_sweep_reclaims_three_athlete_task_types(session_factory):
    """seed 3 条运动员任务 processing + started_at 回拨 TTL，
    sweep 后三条都应变成 failed / error_message='orphan recovered on worker restart'。
    """
    await _cleanup(session_factory)
    try:
        settings = get_settings()
        ttl_seconds = settings.orphan_task_timeout_seconds
        stale_start = now_cst() - timedelta(seconds=ttl_seconds + 60)

        task_ids: list[uuid.UUID] = []
        async with session_factory() as session:
            for task_type in (
                TaskType.athlete_video_classification,
                TaskType.athlete_video_preprocessing,
                TaskType.athlete_diagnosis,
            ):
                row = AnalysisTask(
                    id=uuid.uuid4(),
                    task_type=task_type,
                    video_filename=f"{_TAG}.mp4",
                    video_size_bytes=1024,
                    video_storage_uri=f"charhuang/{_TAG}/{task_type.value}.mp4",
                    status=TaskStatus.processing,
                    started_at=stale_start,
                    submitted_via="single",
                )
                session.add(row)
                task_ids.append(row.id)
            await session.commit()

        # Trigger sweep
        from src.workers.orphan_recovery import sweep_orphan_tasks
        reclaimed = await sweep_orphan_tasks()
        assert reclaimed >= 3, f"Expected at least 3 reclaimed, got {reclaimed}"

        # Verify all 3 tasks flipped to failed with expected error_message
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(AnalysisTask).where(AnalysisTask.id.in_(task_ids))
                )
            ).scalars().all()
            assert len(rows) == 3
            for row in rows:
                assert row.status == TaskStatus.failed, (
                    f"task {row.task_type} still in {row.status}"
                )
                assert row.error_message == "orphan recovered on worker restart"
                assert row.completed_at is not None
    finally:
        await _cleanup(session_factory)


@pytest.mark.asyncio
async def test_orphan_sweep_reclaims_stale_preprocessing_job(session_factory):
    """Feature-016 级联：video_preprocessing_jobs 在 running 卡住也应被回收。"""
    await _cleanup(session_factory)
    try:
        settings = get_settings()
        ttl_seconds = settings.orphan_task_timeout_seconds
        stale_start = now_cst() - timedelta(seconds=ttl_seconds + 60)

        cos_key = f"charhuang/tt_video/{_TAG}/preprocessing.mp4"
        async with session_factory() as session:
            job = VideoPreprocessingJob(
                cos_object_key=cos_key,
                status="running",
                started_at=stale_start,
                business_phase="INFERENCE",
                business_step="preprocess_athlete_video",
            )
            session.add(job)
            await session.commit()
            job_id = job.id

        from src.workers.orphan_recovery import sweep_orphan_tasks
        reclaimed = await sweep_orphan_tasks()
        assert reclaimed >= 1

        async with session_factory() as session:
            refreshed = (
                await session.execute(
                    select(VideoPreprocessingJob).where(
                        VideoPreprocessingJob.id == job_id
                    )
                )
            ).scalar_one()
            assert refreshed.status == "failed"
            assert refreshed.error_message == "orphan_recovered"
            assert refreshed.completed_at is not None
    finally:
        await _cleanup(session_factory)


@pytest.mark.asyncio
async def test_fresh_running_tasks_not_reclaimed(session_factory):
    """started_at 在 TTL 以内的 processing 任务不应被误回收。"""
    await _cleanup(session_factory)
    try:
        fresh_start = now_cst() - timedelta(seconds=5)  # 刚启动 5 秒
        async with session_factory() as session:
            row = AnalysisTask(
                id=uuid.uuid4(),
                task_type=TaskType.athlete_diagnosis,
                video_filename=f"{_TAG}.mp4",
                video_size_bytes=1024,
                video_storage_uri=f"charhuang/{_TAG}/fresh.mp4",
                status=TaskStatus.processing,
                started_at=fresh_start,
                submitted_via="single",
            )
            session.add(row)
            await session.commit()
            fresh_id = row.id

        from src.workers.orphan_recovery import sweep_orphan_tasks
        await sweep_orphan_tasks()

        async with session_factory() as session:
            refreshed = (
                await session.execute(
                    select(AnalysisTask).where(AnalysisTask.id == fresh_id)
                )
            ).scalar_one()
            # 未超 TTL 的仍然处于 processing
            assert refreshed.status == TaskStatus.processing
            assert refreshed.error_message is None
    finally:
        await _cleanup(session_factory)
