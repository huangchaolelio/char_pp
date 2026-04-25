"""Integration test — Feature 014 DAG end-to-end with scaffold executors (US1 T022).

Verifies:
  - Orchestrator.create_job inserts ExtractionJob + 6 PipelineStep rows.
  - Orchestrator.run drives all 6 steps to terminal states.
  - Dependencies are respected (download_video completes before pose/audio).
  - Final job status = success when all steps succeed.
  - coach_video_classifications.kb_extracted = TRUE after merge_kb.

Requires:
  - PostgreSQL with migration 0013 applied.
  - COS download is *patched* — we don't hit the real COS.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.models.extraction_job import ExtractionJob, ExtractionJobStatus
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.services.kb_extraction_pipeline.orchestrator import Orchestrator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _stub_algorithm_executors(monkeypatch) -> None:
    """Stub Feature-015 algorithm executors with scaffold-style returns.

    Feature-014 DAG tests use 1-byte placeholder mp4 files that cannot pass
    the real pose/audio quality gates introduced in Feature-015. These tests
    exercise orchestration, not algorithm correctness, so we short-circuit
    the heavy executors here.

    Feature-016 US2 changed download_video to consume preprocessing segments
    (not the raw COS object). DAG tests don't seed preprocessing jobs, so
    stub download_video too.
    """
    from pathlib import Path as _Path

    from src.models.pipeline_step import PipelineStepStatus
    from src.services.kb_extraction_pipeline.step_executors import (
        audio_kb_extract,
        audio_transcription,
        download_video,
        pose_analysis,
        visual_kb_extract,
    )

    async def _fake_download(session, job, step):
        from src.config import get_settings as _gs
        root = _Path(_gs().extraction_artifact_root) / "jobs" / str(job.id)
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

    async def _fake_pose(session, job, step):
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "keypoints_frame_count": 0,
                "detected_segments": 0,
                "backend": "test_fixture",
            },
            "output_artifact_path": None,
        }

    async def _fake_audio_trans(session, job, step):
        if not job.enable_audio_analysis:
            return {
                "status": PipelineStepStatus.skipped,
                "output_summary": {
                    "skipped": True,
                    "skip_reason": "disabled_by_request",
                    "whisper_model": None,
                },
                "output_artifact_path": None,
            }
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "whisper_model": "test",
                "language_detected": "zh",
                "transcript_chars": 0,
                "skipped": False,
                "skip_reason": None,
            },
            "output_artifact_path": None,
        }

    async def _fake_visual(session, job, step):
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "kb_items": [],
                "kb_items_count": 0,
                "source_type": "visual",
                "tech_category": job.tech_category,
                "backend": "test_fixture",
            },
            "output_artifact_path": None,
        }

    async def _fake_audio_kb(session, job, step):
        from sqlalchemy import select
        from src.models.pipeline_step import PipelineStep, StepType

        upstream = (
            await session.execute(
                select(PipelineStep).where(
                    PipelineStep.job_id == job.id,
                    PipelineStep.step_type == StepType.audio_transcription,
                )
            )
        ).scalar_one()
        if upstream.status == PipelineStepStatus.skipped:
            return {
                "status": PipelineStepStatus.skipped,
                "output_summary": {
                    "skipped": True,
                    "skip_reason": "audio_transcription_skipped",
                    "kb_items": [],
                },
                "output_artifact_path": None,
            }
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "kb_items": [],
                "kb_items_count": 0,
                "source_type": "audio",
                "llm_model": "test_fixture",
            },
            "output_artifact_path": None,
        }

    monkeypatch.setattr(pose_analysis, "execute", _fake_pose, raising=True)
    monkeypatch.setattr(audio_transcription, "execute", _fake_audio_trans, raising=True)
    monkeypatch.setattr(visual_kb_extract, "execute", _fake_visual, raising=True)
    monkeypatch.setattr(audio_kb_extract, "execute", _fake_audio_kb, raising=True)


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_kb_task(session_factory):
    """Create an analysis_tasks row + coach_video_classifications row we can drive.

    Yields ``(task_id, cos_key)``. Cleans up on teardown.
    """
    cos_key = f"tests/feature014/video_{uuid.uuid4().hex[:8]}.mp4"
    task_id: uuid.UUID | None = None

    async with session_factory() as session:
        # Seed the coach_video_classifications row so merge_kb's UPDATE finds it.
        cvc = CoachVideoClassification(
            coach_name="测试教练",
            course_series="feature014-test",
            cos_object_key=cos_key,
            filename=cos_key.rsplit("/", 1)[-1],
            tech_category="forehand_loop_fast",
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

    # Teardown — delete by cos_object_key cascade.
    async with session_factory() as session:
        if task_id:
            # Delete the task (cascades to extraction_jobs -> pipeline_steps / kb_conflicts).
            await session.execute(
                delete(AnalysisTask).where(AnalysisTask.id == task_id)
            )
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key == cos_key
            )
        )
        await session.commit()


class TestPipelineDag:
    async def test_create_job_inserts_six_steps(
        self, session_factory, seeded_kb_task
    ) -> None:
        task_id, cos_key = seeded_kb_task
        async with session_factory() as session:
            job = await Orchestrator.create_job(
                session,
                analysis_task_id=task_id,
                cos_object_key=cos_key,
                tech_category="forehand_loop_fast",
            )
            await session.commit()

            # 6 pipeline_steps rows, one per step_type.
            rows = (
                await session.execute(
                    select(PipelineStep).where(PipelineStep.job_id == job.id)
                )
            ).scalars().all()
            assert len(rows) == 6
            assert {r.step_type for r in rows} == set(StepType)
            assert all(r.status == PipelineStepStatus.pending for r in rows)

            # analysis_tasks row should now carry extraction_job_id.
            at = (
                await session.execute(
                    select(AnalysisTask).where(AnalysisTask.id == task_id)
                )
            ).scalar_one()
            assert at.extraction_job_id == job.id

    async def test_run_completes_all_steps_success(
        self, session_factory, seeded_kb_task, tmp_path, monkeypatch
    ) -> None:
        task_id, cos_key = seeded_kb_task

        # Patch the COS client factory so we don't hit the network — any call
        # to ``client.get_object(...)["Body"].get_stream_to_file(path)`` writes
        # a 1-byte placeholder file.
        class _FakeBody:
            def get_stream_to_file(self, path: str) -> None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_bytes(b"\x00")

        class _FakeClient:
            def get_object(self, Bucket, Key):
                return {"Body": _FakeBody()}

        from src.services import cos_client as cos_mod
        monkeypatch.setattr(
            cos_mod, "_get_cos_client", lambda: (_FakeClient(), "test-bucket"), raising=True
        )

        # Stub out the algorithm executors — the 1-byte fake video can't pass
        # Feature-015 pose/audio real-algorithm gates; this test focuses on
        # DAG orchestration, not algorithm correctness.
        _stub_algorithm_executors(monkeypatch)

        # Use a scratch artifact dir per test to stay hermetic.
        settings = get_settings()
        monkeypatch.setattr(settings, "extraction_artifact_root", str(tmp_path), raising=False)

        async with session_factory() as session:
            job = await Orchestrator.create_job(
                session,
                analysis_task_id=task_id,
                cos_object_key=cos_key,
                tech_category="forehand_loop_fast",
            )
            await session.commit()
            job_id = job.id

        async with session_factory() as session:
            orchestrator = Orchestrator()
            final = await orchestrator.run(session, job_id)
            assert final == ExtractionJobStatus.success

            # All 6 steps should be success.
            rows = (
                await session.execute(
                    select(PipelineStep).where(PipelineStep.job_id == job_id)
                )
            ).scalars().all()
            assert len(rows) == 6
            statuses = {r.step_type.value: r.status.value for r in rows}
            assert statuses == {
                "download_video": "success",
                "pose_analysis": "success",
                "audio_transcription": "success",
                "visual_kb_extract": "success",
                "audio_kb_extract": "success",
                "merge_kb": "success",
            }

            # coach_video_classifications.kb_extracted flipped to TRUE.
            kb_flag = (
                await session.execute(
                    select(CoachVideoClassification.kb_extracted).where(
                        CoachVideoClassification.cos_object_key == cos_key
                    )
                )
            ).scalar_one()
            assert kb_flag is True

    async def test_run_with_audio_disabled_skips_audio_path(
        self, session_factory, seeded_kb_task, tmp_path, monkeypatch
    ) -> None:
        task_id, cos_key = seeded_kb_task

        class _FakeBody:
            def get_stream_to_file(self, path: str) -> None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_bytes(b"\x00")

        class _FakeClient:
            def get_object(self, Bucket, Key):
                return {"Body": _FakeBody()}

        from src.services import cos_client as cos_mod
        monkeypatch.setattr(
            cos_mod, "_get_cos_client", lambda: (_FakeClient(), "test-bucket"), raising=True
        )
        _stub_algorithm_executors(monkeypatch)
        settings = get_settings()
        monkeypatch.setattr(settings, "extraction_artifact_root", str(tmp_path), raising=False)

        async with session_factory() as session:
            job = await Orchestrator.create_job(
                session,
                analysis_task_id=task_id,
                cos_object_key=cos_key,
                tech_category="forehand_loop_fast",
                enable_audio_analysis=False,
            )
            await session.commit()
            job_id = job.id

        async with session_factory() as session:
            final = await Orchestrator().run(session, job_id)
            assert final == ExtractionJobStatus.success

            rows = (
                await session.execute(
                    select(PipelineStep).where(PipelineStep.job_id == job_id)
                )
            ).scalars().all()
            statuses = {r.step_type.value: r.status.value for r in rows}
            # Audio path self-skipped; visual path + merge_kb succeed.
            assert statuses["audio_transcription"] == "skipped"
            assert statuses["audio_kb_extract"] == "skipped"
            assert statuses["visual_kb_extract"] == "success"
            assert statuses["merge_kb"] == "success"
