"""Integration test — Feature 014 US3: parallel execution wins on wall-clock (T045+T046).

Verifies (spec SC-002):
  - Wave-2 (pose_analysis ∥ audio_transcription) starts both steps within a
    tight window after download_video finishes (<1s apart).
  - Overall job wall-clock is closer to max(pose, audio) than pose+audio.
  - Savings ≥ 30% vs a naive serial baseline.

Mechanism: monkeypatch pose/audio executors to await ``asyncio.sleep`` with
fixed durations, so a parallel orchestrator produces a wall-clock very close
to max(durations) while a serial one would be close to sum(durations).

Requires PostgreSQL with migration 0013 applied (real DB — the orchestrator
writes step states in real time, which is what lets us measure ``started_at``).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.models.expert_tech_point import ExpertTechPoint
from src.models.extraction_job import ExtractionJob, ExtractionJobStatus
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.models.tech_knowledge_base import TechKnowledgeBase
from src.services.kb_extraction_pipeline.orchestrator import Orchestrator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


POSE_SIMULATED_SECS = 1.5
AUDIO_SIMULATED_SECS = 1.0


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=4,          # needs room for parallel sessions
        max_overflow=4,
        pool_pre_ping=False,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_kb_task(session_factory):
    cos_key = f"tests/f14_us3/video_{uuid.uuid4().hex[:8]}.mp4"
    task_id: uuid.UUID | None = None

    async with session_factory() as session:
        cvc = CoachVideoClassification(
            coach_name="US3测试教练",
            course_series="feature014-us3",
            cos_object_key=cos_key,
            filename=cos_key.rsplit("/", 1)[-1],
            tech_category="forehand_topspin",
            tech_tags=[],
            classification_source="rule",
            confidence=1.0,
            name_source="fallback",
            kb_extracted=False,
        )
        session.add(cvc)

        task = AnalysisTask(
            task_type=TaskType.kb_extraction,
            video_filename=cos_key.rsplit("/", 1)[-1],
            video_size_bytes=1024,
            video_storage_uri=cos_key,
            status=TaskStatus.pending,
            cos_object_key=cos_key,
            submitted_via="single",
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    yield task_id, cos_key

    async with session_factory() as session:
        job_ids = (
            await session.execute(
                select(ExtractionJob.id).where(
                    ExtractionJob.cos_object_key == cos_key
                )
            )
        ).scalars().all()
        if job_ids:
            versions = (
                await session.execute(
                    select(ExpertTechPoint.knowledge_base_version)
                    .where(ExpertTechPoint.source_video_id == task_id)
                    .distinct()
                )
            ).scalars().all()
            await session.execute(
                delete(ExpertTechPoint).where(
                    ExpertTechPoint.source_video_id == task_id
                )
            )
            if versions:
                await session.execute(
                    delete(TechKnowledgeBase).where(
                        TechKnowledgeBase.version.in_(list(versions))
                    )
                )
        await session.execute(
            delete(AnalysisTask).where(AnalysisTask.id == task_id)
        )
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key == cos_key
            )
        )
        await session.commit()


def _install_fake_cos(monkeypatch) -> None:
    from src.services import cos_client as cos_mod

    class _FakeBody:
        def get_stream_to_file(self, path: str) -> None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"\x00")

    class _FakeClient:
        def get_object(self, Bucket, Key):
            return {"Body": _FakeBody()}

    monkeypatch.setattr(
        cos_mod, "_get_cos_client",
        lambda: (_FakeClient(), "test-bucket"),
        raising=True,
    )

    # Feature-016 US2 — download_video no longer pulls COS directly; stub
    # with a minimal success that points to a scratch dir.
    from src.models.pipeline_step import PipelineStepStatus
    from src.services.kb_extraction_pipeline.step_executors import download_video

    async def _fake_download(session, job, step):
        from src.config import get_settings as _gs
        root = Path(_gs().extraction_artifact_root) / "jobs" / str(job.id)
        (root / "segments").mkdir(parents=True, exist_ok=True)
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "video_preprocessing_job_id": None,
                "segments_total": 0,
                "segments_downloaded": 0,
                "audio_downloaded": False,
                "local_cache_hits": 0,
                "cos_downloads": 0,
            },
            "output_artifact_path": str(root),
        }

    monkeypatch.setattr(download_video, "execute", _fake_download, raising=True)


def _patch_scratch_dir(monkeypatch, tmp_path) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        settings, "extraction_artifact_root", str(tmp_path), raising=False
    )


def _install_timed_fakes(monkeypatch, pose_secs: float, audio_secs: float) -> None:
    """Replace pose_analysis / audio_transcription executors with async sleeps.

    Both executors *await* (not block) — a correctly-parallel orchestrator
    overlaps them. A serial driver would not. Each fake writes a minimal
    artifact so downstream extractors don't hit their existence checks.
    """
    from src.services.kb_extraction_pipeline.step_executors import (
        pose_analysis,
        audio_transcription,
    )

    async def _fake_pose(session, job, step):
        await asyncio.sleep(pose_secs)
        from src.config import get_settings as _gs
        root = Path(_gs().extraction_artifact_root) / str(job.id)
        root.mkdir(parents=True, exist_ok=True)
        out = root / "pose.json"
        out.write_text('{"keypoints": [], "kb_items": []}')
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {"backend": "sleep", "slept_s": pose_secs},
            "output_artifact_path": str(out),
        }

    async def _fake_audio(session, job, step):
        if not job.enable_audio_analysis:
            return {
                "status": PipelineStepStatus.skipped,
                "output_summary": {"skipped": True},
                "output_artifact_path": None,
            }
        await asyncio.sleep(audio_secs)
        from src.config import get_settings as _gs
        root = Path(_gs().extraction_artifact_root) / str(job.id)
        root.mkdir(parents=True, exist_ok=True)
        out = root / "transcript.json"
        out.write_text('{"segments": [], "kb_items": []}')
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {"whisper_model": "sleep", "slept_s": audio_secs},
            "output_artifact_path": str(out),
        }

    monkeypatch.setattr(pose_analysis, "execute", _fake_pose, raising=True)
    monkeypatch.setattr(audio_transcription, "execute", _fake_audio, raising=True)


# ── Tests ───────────────────────────────────────────────────────────────────


class TestParallelExecutionTimeline:
    async def test_pose_and_audio_start_within_one_second_of_each_other(
        self, session_factory, seeded_kb_task, tmp_path, monkeypatch
    ) -> None:
        """T046: after download_video completes, pose and audio steps start
        effectively simultaneously (``started_at`` diff < 1s)."""
        task_id, cos_key = seeded_kb_task
        _install_fake_cos(monkeypatch)
        _patch_scratch_dir(monkeypatch, tmp_path)
        _install_timed_fakes(
            monkeypatch,
            pose_secs=POSE_SIMULATED_SECS,
            audio_secs=AUDIO_SIMULATED_SECS,
        )

        async with session_factory() as session:
            job = await Orchestrator.create_job(
                session,
                analysis_task_id=task_id,
                cos_object_key=cos_key,
                tech_category="forehand_topspin",
            )
            await session.commit()
            job_id = job.id

        async with session_factory() as session:
            final = await Orchestrator().run(session, job_id)
            assert final == ExtractionJobStatus.success

            rows = {
                r.step_type: r
                for r in (
                    await session.execute(
                        select(PipelineStep).where(PipelineStep.job_id == job_id)
                    )
                ).scalars().all()
            }
            pose = rows[StepType.pose_analysis]
            audio = rows[StepType.audio_transcription]
            assert pose.started_at is not None
            assert audio.started_at is not None
            gap_s = abs(
                (pose.started_at - audio.started_at).total_seconds()
            )
            assert gap_s < 1.0, (
                f"pose/audio started_at gap = {gap_s:.2f}s "
                "(expected <1s; orchestrator may be serialising waves)"
            )

    async def test_wallclock_close_to_max_not_sum_of_paths(
        self, session_factory, seeded_kb_task, tmp_path, monkeypatch
    ) -> None:
        """T045: observable wall-clock of pose+audio wave ≈ max(times), not sum.

        Serial would take POSE + AUDIO ≈ 2.5s; parallel gets max(POSE, AUDIO)
        ≈ 1.5s plus small orchestrator overhead. We accept anything < 2.0s as
        definitive parallelism (≥ 30% savings from the 2.5s serial baseline).
        """
        task_id, cos_key = seeded_kb_task
        _install_fake_cos(monkeypatch)
        _patch_scratch_dir(monkeypatch, tmp_path)
        _install_timed_fakes(
            monkeypatch,
            pose_secs=POSE_SIMULATED_SECS,
            audio_secs=AUDIO_SIMULATED_SECS,
        )

        async with session_factory() as session:
            job = await Orchestrator.create_job(
                session,
                analysis_task_id=task_id,
                cos_object_key=cos_key,
                tech_category="forehand_topspin",
            )
            await session.commit()
            job_id = job.id

        async with session_factory() as session:
            import time

            wall_start = time.perf_counter()
            final = await Orchestrator().run(session, job_id)
            wall_elapsed = time.perf_counter() - wall_start
            assert final == ExtractionJobStatus.success

            serial_baseline = POSE_SIMULATED_SECS + AUDIO_SIMULATED_SECS
            # SC-002 requires ≥ 30% savings; our 1.5 + 1.0 baseline with
            # parallel max = 1.5 gives 40% savings. Account for ~1s of
            # orchestrator polling + DB I/O overhead.
            savings = (serial_baseline - wall_elapsed) / serial_baseline
            assert savings >= 0.30, (
                f"wall-clock={wall_elapsed:.2f}s "
                f"vs serial baseline={serial_baseline:.2f}s; "
                f"savings={savings*100:.1f}% (require ≥ 30%)"
            )
