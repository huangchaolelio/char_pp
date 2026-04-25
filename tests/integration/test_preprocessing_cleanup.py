"""Unit tests — Feature-016 Phase 7 cleanup mechanisms (T041, T042).

Tests the preprocessing-specific housekeeping helpers in isolation:
- ``_cleanup_preprocessing_local`` removes old preprocessing dirs while
  respecting the 1-hour active-consumer grace window.
- ``_sweep_preprocessing_orphans`` flips stale ``running`` preprocessing
  jobs to ``failed``.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure both preprocessing mappers are registered before any test runs.
# VideoPreprocessingJob has a relationship() to VideoPreprocessingSegment,
# so both must be imported on the module's import path.
import src.models.video_preprocessing_segment  # noqa: F401
import src.models.video_preprocessing_job  # noqa: F401

from src.config import get_settings


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


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
    yield factory
    await engine.dispose()


# ──────────────────────────────────────────────────────────────────────────────
# T041: preprocessing_local dir cleanup
# ──────────────────────────────────────────────────────────────────────────────


class TestPreprocessingLocalCleanup:
    async def test_removes_expired_dir(self, tmp_path, monkeypatch):
        """Dir older than retention window → deleted."""
        from src.workers import housekeeping_task

        # Point extraction_artifact_root at tmp_path.
        settings = get_settings()
        monkeypatch.setattr(
            settings, "extraction_artifact_root", str(tmp_path), raising=False
        )
        monkeypatch.setattr(
            settings, "preprocessing_local_retention_hours", 1, raising=False
        )

        old_dir = tmp_path / "preprocessing" / str(uuid.uuid4())
        old_dir.mkdir(parents=True)
        (old_dir / "seg_0000.mp4").write_bytes(b"stale")

        # Backdate mtime + atime by 2 hours (older than retention).
        two_hours_ago = time.time() - 7200
        os.utime(old_dir, (two_hours_ago, two_hours_ago))

        result = await housekeeping_task._cleanup_preprocessing_local()

        assert result["preprocessing_scanned"] == 1
        assert result["preprocessing_dirs_removed"] == 1
        assert result["preprocessing_skipped_recent"] == 0
        assert not old_dir.exists()

    async def test_keeps_recent_dir(self, tmp_path, monkeypatch):
        """Dir touched within 1-hour grace window → kept."""
        from src.workers import housekeeping_task

        settings = get_settings()
        monkeypatch.setattr(
            settings, "extraction_artifact_root", str(tmp_path), raising=False
        )
        monkeypatch.setattr(
            settings, "preprocessing_local_retention_hours", 1, raising=False
        )

        fresh_dir = tmp_path / "preprocessing" / str(uuid.uuid4())
        fresh_dir.mkdir(parents=True)
        (fresh_dir / "audio.wav").write_bytes(b"fresh")
        # atime = now (implicitly); a KB extraction may be hard-linking.

        result = await housekeeping_task._cleanup_preprocessing_local()

        assert result["preprocessing_scanned"] == 1
        assert result["preprocessing_dirs_removed"] == 0
        assert result["preprocessing_skipped_recent"] == 1
        assert fresh_dir.exists()

    async def test_handles_missing_root(self, tmp_path, monkeypatch):
        """preprocessing/ subtree doesn't exist → returns zeros, no crash."""
        from src.workers import housekeeping_task

        settings = get_settings()
        monkeypatch.setattr(
            settings, "extraction_artifact_root",
            str(tmp_path / "nonexistent"), raising=False,
        )

        result = await housekeeping_task._cleanup_preprocessing_local()
        assert result == {
            "preprocessing_scanned": 0,
            "preprocessing_dirs_removed": 0,
            "preprocessing_skipped_recent": 0,
        }

    async def test_leaves_kb_extraction_dirs_alone(self, tmp_path, monkeypatch):
        """Feature-015 ``<job_id>/`` dirs live at root, NOT under
        ``preprocessing/`` — T041 sweep must not touch them."""
        from src.workers import housekeeping_task

        settings = get_settings()
        monkeypatch.setattr(
            settings, "extraction_artifact_root", str(tmp_path), raising=False
        )
        monkeypatch.setattr(
            settings, "preprocessing_local_retention_hours", 1, raising=False
        )

        # A Feature-015 KB extraction job dir, aged 2h.
        kb_dir = tmp_path / str(uuid.uuid4())
        kb_dir.mkdir()
        (kb_dir / "pose.json").write_text("{}")
        two_hours_ago = time.time() - 7200
        os.utime(kb_dir, (two_hours_ago, two_hours_ago))

        # A Feature-016 preprocessing dir, also aged 2h.
        pp_dir = tmp_path / "preprocessing" / str(uuid.uuid4())
        pp_dir.mkdir(parents=True)
        os.utime(pp_dir, (two_hours_ago, two_hours_ago))

        result = await housekeeping_task._cleanup_preprocessing_local()

        assert kb_dir.exists(), "KB extraction dir must not be touched by T041"
        assert not pp_dir.exists(), "Preprocessing dir should be swept"
        assert result["preprocessing_dirs_removed"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# T042: preprocessing orphan sweep
# ──────────────────────────────────────────────────────────────────────────────


class TestPreprocessingOrphanSweep:
    async def test_reclaims_stale_running_jobs(self, session_factory):
        """A preprocessing job started > ORPHAN_TASK_TIMEOUT_SECONDS ago
        in ``running`` state → flipped to ``failed`` with 'orphan_recovered'."""
        from src.models.video_preprocessing_job import VideoPreprocessingJob
        from src.workers.orphan_recovery import _sweep_preprocessing_orphans

        cos_key = f"tests/feature016_orphan/{uuid.uuid4().hex[:8]}.mp4"
        job_id = uuid.uuid4()

        # Seed a stale running job (started 1 hour ago).
        async with session_factory() as session:
            session.add(
                VideoPreprocessingJob(
                    id=job_id,
                    cos_object_key=cos_key,
                    status="running",
                    started_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),
                    has_audio=False,
                )
            )
            await session.commit()

        # Sweep with cutoff = now - 30 min (older is orphan).
        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=30)
        try:
            async with session_factory() as session:
                count = await _sweep_preprocessing_orphans(session, cutoff)
            # Our seeded job must be among the reclaimed (count >= 1).
            # Real-DB may have other stale running jobs from earlier runs;
            # we only assert OUR row transitioned correctly.
            assert count >= 1

            # Verify the job is now failed.
            async with session_factory() as session:
                row = (
                    await session.execute(
                        select(VideoPreprocessingJob).where(
                            VideoPreprocessingJob.id == job_id
                        )
                    )
                ).scalar_one()
                assert row.status == "failed"
                assert row.error_message == "orphan_recovered"
                assert row.completed_at is not None
        finally:
            async with session_factory() as session:
                await session.execute(
                    delete(VideoPreprocessingJob).where(
                        VideoPreprocessingJob.id == job_id
                    )
                )
                await session.commit()

    async def test_leaves_fresh_running_jobs_alone(self, session_factory):
        """A recently-started running job → not reclaimed."""
        from src.models.video_preprocessing_job import VideoPreprocessingJob
        from src.workers.orphan_recovery import _sweep_preprocessing_orphans

        cos_key = f"tests/feature016_orphan/{uuid.uuid4().hex[:8]}_fresh.mp4"
        job_id = uuid.uuid4()

        # Seed a fresh running job (started 1 minute ago).
        async with session_factory() as session:
            session.add(
                VideoPreprocessingJob(
                    id=job_id,
                    cos_object_key=cos_key,
                    status="running",
                    started_at=datetime.now(tz=timezone.utc) - timedelta(minutes=1),
                    has_audio=False,
                )
            )
            await session.commit()

        # Cutoff = 30 minutes ago → fresh job should NOT be reclaimed.
        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=30)
        try:
            async with session_factory() as session:
                count = await _sweep_preprocessing_orphans(session, cutoff)
            assert count == 0

            async with session_factory() as session:
                row = (
                    await session.execute(
                        select(VideoPreprocessingJob).where(
                            VideoPreprocessingJob.id == job_id
                        )
                    )
                ).scalar_one()
                assert row.status == "running"
                assert row.error_message is None
        finally:
            async with session_factory() as session:
                await session.execute(
                    delete(VideoPreprocessingJob).where(
                        VideoPreprocessingJob.id == job_id
                    )
                )
                await session.commit()

    async def test_does_not_touch_terminal_states(self, session_factory):
        """success / failed / superseded jobs are immutable — even old ones."""
        from src.models.video_preprocessing_job import VideoPreprocessingJob
        from src.workers.orphan_recovery import _sweep_preprocessing_orphans

        cos_prefix = f"tests/feature016_orphan/{uuid.uuid4().hex[:8]}"
        job_ids: dict[str, uuid.UUID] = {}
        ancient = datetime.now(tz=timezone.utc) - timedelta(days=1)

        async with session_factory() as session:
            for status in ("success", "failed", "superseded"):
                jid = uuid.uuid4()
                job_ids[status] = jid
                session.add(
                    VideoPreprocessingJob(
                        id=jid,
                        cos_object_key=f"{cos_prefix}_{status}.mp4",
                        status=status,
                        started_at=ancient,
                        completed_at=ancient,
                        has_audio=False,
                    )
                )
            await session.commit()

        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=30)
        try:
            async with session_factory() as session:
                count = await _sweep_preprocessing_orphans(session, cutoff)
            assert count == 0  # none match status='running'

            async with session_factory() as session:
                for status, jid in job_ids.items():
                    row = (
                        await session.execute(
                            select(VideoPreprocessingJob).where(
                                VideoPreprocessingJob.id == jid
                            )
                        )
                    ).scalar_one()
                    assert row.status == status
                    assert row.error_message is None
        finally:
            async with session_factory() as session:
                for jid in job_ids.values():
                    await session.execute(
                        delete(VideoPreprocessingJob).where(
                            VideoPreprocessingJob.id == jid
                        )
                    )
                await session.commit()
