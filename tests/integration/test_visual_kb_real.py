"""Integration test — Feature 015 visual_kb_extract with synthesized artifact (T008).

Drives ``visual_kb_extract`` with a hand-crafted pose.json fixture containing
realistic forehand-swing keypoints, verifies it calls the Feature-002 chain
(action_segmenter → action_classifier → tech_extractor) and emits non-empty
kb_items with ``source_type='visual'``.

Does **not** run a real pose estimator — that is covered in deployment
verification. The goal here is to validate the wiring in a CI-friendly way.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.models.extraction_job import ExtractionJob, ExtractionJobStatus
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.services.kb_extraction_pipeline.artifact_io import write_pose_artifact
from src.services.pose_estimator import FramePoseResult, Keypoint


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Pose fixture synthesis ──────────────────────────────────────────────────


def _synthesize_forehand_swing_frames(
    num_frames: int = 60, fps: int = 30
) -> list[FramePoseResult]:
    """Produce a sequence of FramePoseResult simulating a forehand swing.

    The geometry matters for ``action_classifier`` + ``tech_extractor``:
      - Right shoulder (12) / right elbow (14) / right wrist (16) move along
        an arc so swing_trajectory + elbow_angle can be computed.
      - Hips (23, 24) shift laterally so weight_transfer is detectable.
      - Visibility stays >=0.7 so tech_extractor's confidence threshold passes.

    We produce enough frames (60 @ 30fps = 2s) that action_segmenter detects
    at least one high-velocity segment.
    """
    frames: list[FramePoseResult] = []
    for i in range(num_frames):
        # Swing phase: wrist arcs from right-back to left-front
        t = i / max(1, num_frames - 1)  # 0.0 → 1.0
        # Right arm drawing a rough arc:
        angle = math.pi * t  # 0 → pi
        wrist_x = 0.3 + 0.5 * math.sin(angle)
        wrist_y = 0.4 - 0.2 * math.cos(angle)
        elbow_x = 0.35 + 0.2 * math.sin(angle)
        elbow_y = 0.45
        shoulder_r_x = 0.55
        shoulder_r_y = 0.35
        shoulder_l_x = 0.45
        shoulder_l_y = 0.35
        # Hips shift from right (0.55) to left (0.45) during the swing.
        hip_r_x = 0.55 - 0.1 * t
        hip_l_x = 0.45 - 0.1 * t

        keypoints = {
            11: Keypoint(shoulder_l_x, shoulder_l_y, 0.0, 0.92),  # LEFT_SHOULDER
            12: Keypoint(shoulder_r_x, shoulder_r_y, 0.0, 0.92),  # RIGHT_SHOULDER
            13: Keypoint(0.42, 0.48, 0.0, 0.88),                   # LEFT_ELBOW
            14: Keypoint(elbow_x, elbow_y, 0.0, 0.90),             # RIGHT_ELBOW
            15: Keypoint(0.40, 0.58, 0.0, 0.85),                   # LEFT_WRIST
            16: Keypoint(wrist_x, wrist_y, 0.0, 0.88),             # RIGHT_WRIST
            23: Keypoint(hip_l_x, 0.60, 0.0, 0.93),                # LEFT_HIP
            24: Keypoint(hip_r_x, 0.60, 0.0, 0.93),                # RIGHT_HIP
        }
        frames.append(
            FramePoseResult(
                frame_index=i,
                timestamp_ms=int(i * (1000 / fps)),
                keypoints=keypoints,
                frame_confidence=0.90,
            )
        )
    return frames


# ── Fixtures ────────────────────────────────────────────────────────────────


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


@pytest_asyncio.fixture
async def seeded_kb_task(session_factory):
    import uuid as _uuid

    cos_key = f"tests/f015_us1/video_{_uuid.uuid4().hex[:8]}.mp4"
    task_id = None

    async with session_factory() as session:
        session.add(
            CoachVideoClassification(
                coach_name="F015视觉测试",
                course_series="feature015-us1",
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

    yield task_id, cos_key

    async with session_factory() as session:
        await session.execute(delete(AnalysisTask).where(AnalysisTask.id == task_id))
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key == cos_key
            )
        )
        await session.commit()


# ── Tests ───────────────────────────────────────────────────────────────────


async def test_visual_kb_extract_produces_visual_items_from_synthetic_pose(
    session_factory, seeded_kb_task, tmp_path
) -> None:
    """SC-001 visual portion + FR-004/FR-005: visual_kb_extract reads a
    realistic pose.json fixture → emits kb_items with source_type='visual'
    and action_type derived from the classified segment."""
    task_id, cos_key = seeded_kb_task

    # Seed an ExtractionJob + 6 steps; pose_analysis step is pre-success
    # with our synthetic pose.json artifact.
    async with session_factory() as session:
        job = ExtractionJob(
            analysis_task_id=task_id,
            cos_object_key=cos_key,
            tech_category="forehand_topspin",
            status=ExtractionJobStatus.running,
            enable_audio_analysis=False,
            audio_language="zh",
            force=False,
        )
        session.add(job)
        await session.flush()

        # Synthesize pose.json
        frames = _synthesize_forehand_swing_frames(num_frames=60, fps=30)
        pose_path = tmp_path / "pose.json"
        write_pose_artifact(
            pose_path,
            video_path=str(tmp_path / "video.mp4"),
            video_meta={"fps": 30.0, "width": 1920, "height": 1080,
                        "duration_seconds": 2.0, "frame_count": 60},
            backend="mediapipe",
            frames=frames,
        )

        session.add(
            PipelineStep(
                job_id=job.id,
                step_type=StepType.download_video,
                status=PipelineStepStatus.success,
            )
        )
        session.add(
            PipelineStep(
                job_id=job.id,
                step_type=StepType.pose_analysis,
                status=PipelineStepStatus.success,
                output_artifact_path=str(pose_path),
                output_summary={"keypoints_frame_count": 60, "backend": "mediapipe"},
            )
        )
        # Still-pending downstream steps
        visual_step_id = None
        for step_type in (
            StepType.audio_transcription,
            StepType.visual_kb_extract,
            StepType.audio_kb_extract,
            StepType.merge_kb,
        ):
            ps = PipelineStep(job_id=job.id, step_type=step_type)
            session.add(ps)
            await session.flush()
            if step_type == StepType.visual_kb_extract:
                visual_step_id = ps.id

        await session.commit()
        job_id = job.id

    # Run visual_kb_extract directly
    from src.services.kb_extraction_pipeline.step_executors import visual_kb_extract

    async with session_factory() as session:
        visual_step = (
            await session.execute(
                select(PipelineStep).where(PipelineStep.id == visual_step_id)
            )
        ).scalar_one()
        fresh_job = (
            await session.execute(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
        ).scalar_one()

        result = await visual_kb_extract.execute(session, fresh_job, visual_step)

    # ── Assertions ─────────────────────────────────────────────────────────
    assert result["status"] == PipelineStepStatus.success
    summary = result["output_summary"]
    assert summary["source_type"] == "visual"
    assert summary.get("backend") != "scaffold", (
        f"backend should reflect real algorithm, got {summary.get('backend')!r}"
    )
    # SC-001 visual portion: at least one kb_item
    kb_items = summary.get("kb_items", [])
    assert len(kb_items) >= 1, (
        f"expected >=1 visual kb_items from synthetic swing, got {len(kb_items)}"
    )
    # Every item must be well-formed per data-model.md § kb_items
    for item in kb_items:
        assert item["source_type"] == "visual"
        assert item["dimension"] in {
            "elbow_angle", "swing_trajectory", "contact_timing", "weight_transfer",
        }
        assert item["param_min"] <= item["param_ideal"] <= item["param_max"]
        assert 0.0 <= item["extraction_confidence"] <= 1.0
        assert item["extraction_confidence"] >= 0.7, "tech_extractor threshold"
        assert item["action_type"]  # non-empty

    # Cleanup
    async with session_factory() as session:
        await session.execute(delete(ExtractionJob).where(ExtractionJob.id == job_id))
        await session.commit()


async def test_visual_kb_extract_handles_empty_pose_artifact(
    session_factory, seeded_kb_task, tmp_path
) -> None:
    """Degradation: empty pose.json (no frames) → empty kb_items, no crash."""
    task_id, cos_key = seeded_kb_task
    async with session_factory() as session:
        job = ExtractionJob(
            analysis_task_id=task_id,
            cos_object_key=cos_key,
            tech_category="forehand_topspin",
            status=ExtractionJobStatus.running,
            enable_audio_analysis=False,
            audio_language="zh",
            force=False,
        )
        session.add(job)
        await session.flush()

        pose_path = tmp_path / "empty_pose.json"
        pose_path.write_text('{"frames": [], "backend": "mediapipe", "video_meta": {}}')

        session.add(
            PipelineStep(
                job_id=job.id, step_type=StepType.download_video,
                status=PipelineStepStatus.success,
            )
        )
        session.add(
            PipelineStep(
                job_id=job.id, step_type=StepType.pose_analysis,
                status=PipelineStepStatus.success,
                output_artifact_path=str(pose_path),
            )
        )
        visual_step_id = None
        for step_type in (
            StepType.audio_transcription, StepType.visual_kb_extract,
            StepType.audio_kb_extract, StepType.merge_kb,
        ):
            ps = PipelineStep(job_id=job.id, step_type=step_type)
            session.add(ps)
            await session.flush()
            if step_type == StepType.visual_kb_extract:
                visual_step_id = ps.id
        await session.commit()
        job_id = job.id

    from src.services.kb_extraction_pipeline.step_executors import visual_kb_extract

    async with session_factory() as session:
        visual_step = (
            await session.execute(
                select(PipelineStep).where(PipelineStep.id == visual_step_id)
            )
        ).scalar_one()
        fresh_job = (
            await session.execute(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
        ).scalar_one()
        result = await visual_kb_extract.execute(session, fresh_job, visual_step)

    # Still success but empty — downstream merge_kb will degrade.
    assert result["status"] == PipelineStepStatus.success
    assert result["output_summary"]["kb_items"] == []

    async with session_factory() as session:
        await session.execute(delete(ExtractionJob).where(ExtractionJob.id == job_id))
        await session.commit()
