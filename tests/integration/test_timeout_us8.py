"""Integration test — Feature 014 timeout handling (T062).

Verifies (FR-020):
  - A pipeline step that ``asyncio.sleep``s longer than the configured step
    timeout gets aborted by ``asyncio.wait_for`` inside ``_execute_step``,
    the step row lands in ``failed`` with the step-timeout error message,
    and the job ends ``failed``.

We use an artificially tiny ``extraction_step_timeout_seconds`` so the test
finishes in ~1 second instead of 10 minutes.
"""

from __future__ import annotations

import asyncio
import uuid
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


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=4,
        max_overflow=4,
        pool_pre_ping=False,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield factory
    await engine.dispose()


async def test_step_timeout_fails_step_and_job(
    session_factory, tmp_path, monkeypatch
) -> None:
    settings = get_settings()
    # Force a 1-second per-step timeout for this test.
    monkeypatch.setattr(settings, "extraction_step_timeout_seconds", 1, raising=False)
    monkeypatch.setattr(
        settings, "extraction_artifact_root", str(tmp_path), raising=False
    )

    # Fake COS → 1-byte file.
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

    # Feature-016 US2 — stub download_video (it now requires a preprocessing
    # job row, which this test does not seed).
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

    # Replace pose_analysis with a sleeper that exceeds the step timeout.
    from src.services.kb_extraction_pipeline.step_executors import pose_analysis

    async def _sleepy_pose(session, job, step):
        # Sleep well past the 1s timeout so wait_for fires.
        await asyncio.sleep(5.0)
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {},
            "output_artifact_path": None,
        }

    monkeypatch.setattr(pose_analysis, "execute", _sleepy_pose, raising=True)

    # Seed DB.
    cos_key = f"tests/f14_timeout/video_{uuid.uuid4().hex[:8]}.mp4"
    task_id: uuid.UUID | None = None
    async with session_factory() as session:
        session.add(
            CoachVideoClassification(
                coach_name="超时测试教练",
                course_series="feature014-timeout",
                cos_object_key=cos_key,
                filename=cos_key.rsplit("/", 1)[-1],
                tech_category="forehand_topspin",
                tech_tags=[],
                classification_source="rule",
                confidence=1.0,
                name_source="fallback",
                kb_extracted=False,
            )
        )
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

    try:
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

        # Job ended failed.
        assert final == ExtractionJobStatus.failed

        async with session_factory() as session:
            rows = {
                s.step_type: s
                for s in (
                    await session.execute(
                        select(PipelineStep).where(PipelineStep.job_id == job_id)
                    )
                ).scalars().all()
            }
            # pose_analysis was the one that timed out.
            pose = rows[StepType.pose_analysis]
            assert pose.status == PipelineStepStatus.failed
            assert pose.error_message is not None
            assert "timeout" in pose.error_message.lower()

            # Its downstream (visual_kb_extract) got skipped.
            assert rows[StepType.visual_kb_extract].status == PipelineStepStatus.skipped
            # merge_kb also cannot succeed because visual is the hard path.
            assert rows[StepType.merge_kb].status in {
                PipelineStepStatus.skipped,
                PipelineStepStatus.pending,   # orchestrator may have left it pending
            }

    finally:
        async with session_factory() as session:
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
