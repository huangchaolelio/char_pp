"""Integration test — Feature 014 US4 continuation semantics (T052, T053).

Verifies that after a rerun, the Orchestrator:
  - SKIPS already-``success`` steps (no executor call, no re-write of their
    output_summary / artifact_path).
  - Only ``pending`` steps (= the failed ones plus their downstream, post-rerun)
    invoke their executors.

Mechanism: after seeding a failed job + issuing rerun via the API, invoke the
orchestrator directly with counting fakes for pose/audio executors and assert
pose runs 0 times (kept as success) while audio runs exactly 1 time (reset
to pending by rerun).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from src.utils.time_utils import now_cst
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
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


async def test_rerun_skips_success_steps_end_to_end(
    session_factory, tmp_path, monkeypatch
) -> None:
    """End-to-end continuation:
      1. Seed a ``failed`` job where download/pose/visual succeeded.
      2. Rerun via HTTP (resets audio_transcription + audio_kb_extract +
         merge_kb to pending).
      3. Invoke Orchestrator.run directly with counting fake executors.
      4. Assert pose executor was NOT called again; audio-side executors
         each ran exactly once; merge_kb ran once and the job ended success.
    """
    # ── Counters captured in outer scope ────────────────────────────────────
    calls: dict[str, int] = {
        "download_video": 0,
        "pose_analysis": 0,
        "audio_transcription": 0,
        "visual_kb_extract": 0,
        "audio_kb_extract": 0,
        "merge_kb": 0,
    }

    # Patch artifact root so the fake pose / transcript files land here.
    settings = get_settings()
    monkeypatch.setattr(
        settings, "extraction_artifact_root", str(tmp_path), raising=False
    )

    # ── Counting fake executors ─────────────────────────────────────────────
    from src.services.kb_extraction_pipeline.step_executors import (
        audio_kb_extract,
        audio_transcription,
        download_video,
        merge_kb,
        pose_analysis,
        visual_kb_extract,
    )

    # These three should NEVER be invoked on the rerun — their rows stay success.
    async def _never_download(session, job, step):
        calls["download_video"] += 1
        raise AssertionError(
            "download_video executor was invoked — orchestrator should have "
            "skipped it because the step is already success"
        )

    async def _never_pose(session, job, step):
        calls["pose_analysis"] += 1
        raise AssertionError("pose_analysis executor was invoked after rerun")

    async def _never_visual(session, job, step):
        calls["visual_kb_extract"] += 1
        raise AssertionError("visual_kb_extract executor was invoked after rerun")

    # These three should run once.
    async def _fake_audio(session, job, step):
        calls["audio_transcription"] += 1
        # Write a minimal transcript artifact so downstream does not fail.
        root = Path(settings.extraction_artifact_root) / str(job.id)
        root.mkdir(parents=True, exist_ok=True)
        out = root / "transcript.json"
        out.write_text('{"segments": [], "kb_items": []}')
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {"whisper_model": "fake", "kb_items": []},
            "output_artifact_path": str(out),
        }

    async def _fake_audio_kb(session, job, step):
        calls["audio_kb_extract"] += 1
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "kb_items": [],
                "kb_items_count": 0,
                "source_type": "audio",
                "llm_model": "fake",
            },
            "output_artifact_path": None,
        }

    # Use the real merge_kb — it should find the pre-existing visual kb_items
    # from the success step's output_summary and upsert them.
    real_merge = merge_kb.execute

    async def _counted_merge(session, job, step):
        calls["merge_kb"] += 1
        return await real_merge(session, job, step)

    monkeypatch.setattr(download_video, "execute", _never_download, raising=True)
    monkeypatch.setattr(pose_analysis, "execute", _never_pose, raising=True)
    monkeypatch.setattr(visual_kb_extract, "execute", _never_visual, raising=True)
    monkeypatch.setattr(audio_transcription, "execute", _fake_audio, raising=True)
    monkeypatch.setattr(audio_kb_extract, "execute", _fake_audio_kb, raising=True)
    monkeypatch.setattr(merge_kb, "execute", _counted_merge, raising=True)

    # ── Seed DB state as if the first run partially succeeded ───────────────
    cos_key = f"tests/f14_us4_cont/video_{uuid.uuid4().hex[:8]}.mp4"
    async with session_factory() as session:
        cvc = CoachVideoClassification(
            coach_name="US4续跑教练",
            course_series="feature014-us4-cont",
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
            status=TaskStatus.failed,
            cos_object_key=cos_key,
            submitted_via="single",
        )
        session.add(task)
        await session.flush()
        task_id = task.id

        job = ExtractionJob(
            analysis_task_id=task_id,
            cos_object_key=cos_key,
            tech_category="forehand_topspin",
            status=ExtractionJobStatus.failed,
            enable_audio_analysis=True,
            audio_language="zh",
            force=False,
            error_message="simulated",
            started_at=now_cst() - timedelta(minutes=10),
            completed_at=now_cst() - timedelta(minutes=5),
            intermediate_cleanup_at=now_cst()
            + timedelta(hours=23),
        )
        session.add(job)
        await session.flush()
        job_id = job.id

        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == task_id)
            .values(extraction_job_id=job_id)
        )

        # Seed visual kb_items in the visual step so real merge_kb has work.
        visual_summary = {
            "kb_items_count": 1,
            "kb_items": [
                {
                    "dimension": "elbow_angle",
                    "param_min": 90, "param_max": 120, "param_ideal": 105,
                    "unit": "°",
                    "extraction_confidence": 0.9,
                    "action_type": "forehand_topspin",
                }
            ],
            "source_type": "visual",
            "tech_category": "forehand_topspin",
        }
        step_states: list[tuple[StepType, PipelineStepStatus, dict | None, str | None, str | None]] = [
            (
                StepType.download_video,
                PipelineStepStatus.success,
                {"video_size_bytes": 1024},
                str(tmp_path / "video.mp4"),
                None,
            ),
            (
                StepType.pose_analysis,
                PipelineStepStatus.success,
                {"backend": "test"},
                str(tmp_path / "pose.json"),
                None,
            ),
            (
                StepType.audio_transcription,
                PipelineStepStatus.failed,
                None,
                None,
                "simulated failure",
            ),
            (StepType.visual_kb_extract, PipelineStepStatus.success, visual_summary, None, None),
            (StepType.audio_kb_extract, PipelineStepStatus.skipped, None, None, None),
            (StepType.merge_kb, PipelineStepStatus.skipped, None, None, None),
        ]
        for st, status, summary, artifact, err in step_states:
            session.add(
                PipelineStep(
                    job_id=job_id,
                    step_type=st,
                    status=status,
                    output_summary=summary,
                    output_artifact_path=artifact,
                    error_message=err,
                    started_at=now_cst() - timedelta(minutes=6),
                    completed_at=now_cst() - timedelta(minutes=5),
                )
            )
        await session.commit()

    # ── Simulate the rerun path (reset failed + downstream rows) ────────────
    async with session_factory() as session:
        await session.execute(
            update(PipelineStep)
            .where(
                PipelineStep.job_id == job_id,
                PipelineStep.step_type.in_(
                    [
                        StepType.audio_transcription,
                        StepType.audio_kb_extract,
                        StepType.merge_kb,
                    ]
                ),
            )
            .values(
                status=PipelineStepStatus.pending,
                started_at=None,
                completed_at=None,
                duration_ms=None,
                error_message=None,
                retry_count=0,
            )
        )
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(
                status=ExtractionJobStatus.running,
                error_message=None,
                completed_at=None,
                intermediate_cleanup_at=None,
            )
        )
        await session.commit()

    # ── Drive the orchestrator ──────────────────────────────────────────────
    async with session_factory() as session:
        final = await Orchestrator().run(session, job_id)

    # ── Assert ──────────────────────────────────────────────────────────────
    assert final == ExtractionJobStatus.success, (
        f"orchestrator did not recover the job (final={final})"
    )
    # Success steps were NOT re-run (executors would have raised AssertionError).
    assert calls["download_video"] == 0
    assert calls["pose_analysis"] == 0
    assert calls["visual_kb_extract"] == 0
    # Reset steps ran exactly once.
    assert calls["audio_transcription"] == 1
    assert calls["audio_kb_extract"] == 1
    assert calls["merge_kb"] == 1

    # ── DB state ────────────────────────────────────────────────────────────
    async with session_factory() as session:
        rows = {
            s.step_type: s
            for s in (
                await session.execute(
                    select(PipelineStep).where(PipelineStep.job_id == job_id)
                )
            ).scalars().all()
        }
        for st in StepType:
            assert rows[st].status == PipelineStepStatus.success, (
                f"step {st.value} ended in {rows[st].status.value}"
            )
        # The pre-existing visual artifact/summary was NOT wiped.
        assert rows[StepType.visual_kb_extract].output_summary is not None
        assert rows[StepType.visual_kb_extract].output_summary["kb_items_count"] == 1

        # merge_kb actually inserted the visual item.
        points = (
            await session.execute(
                select(ExpertTechPoint).where(
                    ExpertTechPoint.source_video_id == task_id
                )
            )
        ).scalars().all()
        assert len(points) == 1
        assert points[0].dimension == "elbow_angle"
        assert points[0].source_type == "visual"

        # Cleanup.
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
