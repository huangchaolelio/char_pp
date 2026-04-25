"""Integration test — Feature 014 US2: visual + audio KB extraction end-to-end.

Covers:
  T034: End-to-end merge produces both visual- and audio-source ExpertTechPoints.
  T035: Conflicts land in ``kb_conflicts`` and are excluded from the main KB.
  T037: Audio-unavailable degradation → visual-only KB, no audio rows.

Mechanism: monkeypatch the upstream ``visual_kb_extract`` / ``audio_kb_extract``
executors to return crafted ``kb_items`` lists. The rest of the pipeline
(download, pose, transcription, merge_kb) runs normally against the real DB.
"""

from __future__ import annotations

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
from src.models.kb_conflict import KbConflict
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.models.tech_knowledge_base import TechKnowledgeBase
from src.services.kb_extraction_pipeline.orchestrator import Orchestrator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


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
    """A kb_extraction analysis_tasks row + classification row ready for F-014.

    Yields ``(task_id, cos_key)``. Cleans up related KB / conflict rows on teardown
    so consecutive tests don't leak versions.
    """
    cos_key = f"tests/f14_us2/video_{uuid.uuid4().hex[:8]}.mp4"
    task_id: uuid.UUID | None = None

    async with session_factory() as session:
        cvc = CoachVideoClassification(
            coach_name="US2测试教练",
            course_series="feature014-us2",
            cos_object_key=cos_key,
            filename=cos_key.rsplit("/", 1)[-1],
            # Use an ActionType-compatible value so the merger's coerce step
            # can map it onto the F-002 action enum.
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

    # Teardown — delete in dependency order.
    async with session_factory() as session:
        # Resolve any KB version created by this task so we can drop it.
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
    """Replace _get_cos_client so download_video doesn't hit the network.

    Feature-016 US2 also replaces ``download_video.execute`` with a stub that
    synthesises an empty download dir (no preprocessing job required).
    """
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

    # US2 stub — download_video no longer pulls from COS directly; it loads
    # a preprocessing view. Integration tests don't seed preprocessing jobs,
    # so provide a minimal synthesised success response.
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


def _install_fake_upstream_executors(monkeypatch) -> None:
    """Stub pose_analysis and audio_transcription so DAG tests don't need real
    video content. The downstream visual/audio kb executors are patched
    separately via ``_patch_visual_and_audio_items``."""
    from src.models.pipeline_step import PipelineStepStatus
    from src.services.kb_extraction_pipeline.step_executors import (
        pose_analysis,
        audio_transcription,
    )

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

    monkeypatch.setattr(pose_analysis, "execute", _fake_pose, raising=True)
    monkeypatch.setattr(audio_transcription, "execute", _fake_audio_trans, raising=True)


def _patch_scratch_dir(monkeypatch, tmp_path) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        settings, "extraction_artifact_root", str(tmp_path), raising=False
    )


async def _patch_visual_and_audio_items(
    monkeypatch,
    visual_items: list[dict],
    audio_items: list[dict],
) -> None:
    """Override the US1 scaffold executors so they surface our test kb_items."""
    from src.services.kb_extraction_pipeline.step_executors import (
        visual_kb_extract,
        audio_kb_extract,
    )

    async def _fake_visual(session, job, step):
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "kb_items": list(visual_items),
                "kb_items_count": len(visual_items),
                "source_type": "visual",
                "tech_category": job.tech_category,
                "backend": "test_fixture",
            },
            "output_artifact_path": None,
        }

    async def _fake_audio(session, job, step):
        from sqlalchemy import select as _s
        from src.models.pipeline_step import PipelineStep as _PS
        upstream = (
            await session.execute(
                _s(_PS).where(
                    _PS.job_id == job.id,
                    _PS.step_type == StepType.audio_transcription,
                )
            )
        ).scalar_one()
        if upstream.status == PipelineStepStatus.skipped:
            return {
                "status": PipelineStepStatus.skipped,
                "output_summary": {"skipped": True, "kb_items": []},
                "output_artifact_path": None,
            }
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "kb_items": list(audio_items),
                "kb_items_count": len(audio_items),
                "source_type": "audio",
                "llm_model": "test_fixture",
            },
            "output_artifact_path": None,
        }

    monkeypatch.setattr(visual_kb_extract, "execute", _fake_visual, raising=True)
    monkeypatch.setattr(audio_kb_extract, "execute", _fake_audio, raising=True)


# ── Tests ───────────────────────────────────────────────────────────────────


class TestVisualAndAudioKbExtractE2E:
    async def test_both_paths_populate_tech_points_with_source_type(
        self, session_factory, seeded_kb_task, tmp_path, monkeypatch
    ) -> None:
        """T034: expert_tech_points ends up with at least one visual and one
        audio source_type row after merge_kb runs successfully."""
        task_id, cos_key = seeded_kb_task
        _install_fake_cos(monkeypatch)
        _patch_scratch_dir(monkeypatch, tmp_path)
        _install_fake_upstream_executors(monkeypatch)

        # Visual carries a dimension that the audio path doesn't touch
        # (visual-only). Audio carries a different dimension (audio-only).
        # A third dimension is seen by both at close-enough values → merged
        # as ``visual+audio``.
        await _patch_visual_and_audio_items(
            monkeypatch,
            visual_items=[
                {
                    "dimension": "elbow_angle",
                    "param_min": 90, "param_max": 120, "param_ideal": 105,
                    "unit": "°",
                    "extraction_confidence": 0.9,
                    "action_type": "forehand_topspin",
                },
                {
                    "dimension": "wrist_arc",
                    "param_min": 0.8, "param_max": 1.4, "param_ideal": 1.1,
                    "unit": "ratio",
                    "extraction_confidence": 0.85,
                    "action_type": "forehand_topspin",
                },
            ],
            audio_items=[
                {
                    "dimension": "elbow_angle",
                    "param_min": 95, "param_max": 115, "param_ideal": 106,
                    "unit": "°",
                    "extraction_confidence": 0.8,
                    "action_type": "forehand_topspin",
                },
                {
                    "dimension": "weight_transfer",
                    "param_min": 0.5, "param_max": 0.9, "param_ideal": 0.7,
                    "unit": "ratio",
                    "extraction_confidence": 0.75,
                    "action_type": "forehand_topspin",
                },
            ],
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

            points = (
                await session.execute(
                    select(ExpertTechPoint).where(
                        ExpertTechPoint.source_video_id == task_id
                    )
                )
            ).scalars().all()
            source_types = {p.source_type for p in points}
            assert "visual" in source_types
            assert "audio" in source_types
            # elbow_angle appears once, merged as visual+audio
            assert "visual+audio" in source_types

            # 3 distinct dimensions persisted: elbow_angle (merged), wrist_arc
            # (visual-only), weight_transfer (audio-only).
            dims = {p.dimension for p in points}
            assert dims == {"elbow_angle", "wrist_arc", "weight_transfer"}

            # No conflict rows were produced.
            conflict_count = (
                await session.execute(
                    select(KbConflict).where(KbConflict.job_id == job_id)
                )
            ).scalars().all()
            assert conflict_count == []


class TestConflictMerge:
    async def test_large_diff_routes_to_kb_conflicts_table(
        self, session_factory, seeded_kb_task, tmp_path, monkeypatch
    ) -> None:
        """T035: when visual + audio disagree beyond 10%, the dimension lands
        in ``kb_conflicts`` and is NOT in ``expert_tech_points``."""
        task_id, cos_key = seeded_kb_task
        _install_fake_cos(monkeypatch)
        _patch_scratch_dir(monkeypatch, tmp_path)
        _install_fake_upstream_executors(monkeypatch)

        await _patch_visual_and_audio_items(
            monkeypatch,
            visual_items=[
                {
                    "dimension": "contact_timing",
                    "param_min": 150, "param_max": 250, "param_ideal": 200,
                    "unit": "ms",
                    "extraction_confidence": 0.9,
                    "action_type": "forehand_topspin",
                },
                # A clean, non-conflicting visual-only dimension that should
                # still make it through.
                {
                    "dimension": "weight_transfer",
                    "param_min": 0.4, "param_max": 0.8, "param_ideal": 0.6,
                    "unit": "ratio",
                    "extraction_confidence": 0.88,
                    "action_type": "forehand_topspin",
                },
            ],
            audio_items=[
                {
                    "dimension": "contact_timing",
                    # 75% diff from visual → conflict
                    "param_min": 300, "param_max": 400, "param_ideal": 350,
                    "unit": "ms",
                    "extraction_confidence": 0.8,
                    "action_type": "forehand_topspin",
                },
            ],
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
            await Orchestrator().run(session, job_id)

            # Conflict row exists and is unresolved + not superseded.
            conflicts = (
                await session.execute(
                    select(KbConflict).where(KbConflict.job_id == job_id)
                )
            ).scalars().all()
            assert len(conflicts) == 1
            c = conflicts[0]
            assert c.dimension_name == "contact_timing"
            assert c.resolved_at is None
            assert c.superseded_by_job_id is None
            assert c.visual_value is not None
            assert c.audio_value is not None

            # The conflict dimension is NOT in expert_tech_points.
            points = (
                await session.execute(
                    select(ExpertTechPoint).where(
                        ExpertTechPoint.source_video_id == task_id
                    )
                )
            ).scalars().all()
            dims = {p.dimension for p in points}
            assert "contact_timing" not in dims
            # The non-conflicting visual-only dimension still made it in.
            assert "weight_transfer" in dims


class TestAudioUnavailableFallback:
    async def test_audio_disabled_produces_visual_only_kb(
        self, session_factory, seeded_kb_task, tmp_path, monkeypatch
    ) -> None:
        """T037: enable_audio_analysis=False → audio path skipped, merge still
        succeeds with visual-only entries."""
        task_id, cos_key = seeded_kb_task
        _install_fake_cos(monkeypatch)
        _patch_scratch_dir(monkeypatch, tmp_path)
        _install_fake_upstream_executors(monkeypatch)

        await _patch_visual_and_audio_items(
            monkeypatch,
            visual_items=[
                {
                    "dimension": "elbow_angle",
                    "param_min": 90, "param_max": 120, "param_ideal": 105,
                    "unit": "°",
                    "extraction_confidence": 0.9,
                    "action_type": "forehand_topspin",
                },
            ],
            audio_items=[
                # audio_kb_extract should skip before this is seen — we set
                # this to something that WOULD conflict if it were read.
                {
                    "dimension": "elbow_angle",
                    "param_min": 200, "param_max": 220, "param_ideal": 210,
                    "unit": "°",
                    "extraction_confidence": 0.8,
                    "action_type": "forehand_topspin",
                },
            ],
        )

        async with session_factory() as session:
            job = await Orchestrator.create_job(
                session,
                analysis_task_id=task_id,
                cos_object_key=cos_key,
                tech_category="forehand_topspin",
                enable_audio_analysis=False,
            )
            await session.commit()
            job_id = job.id

        async with session_factory() as session:
            final = await Orchestrator().run(session, job_id)
            assert final == ExtractionJobStatus.success

            points = (
                await session.execute(
                    select(ExpertTechPoint).where(
                        ExpertTechPoint.source_video_id == task_id
                    )
                )
            ).scalars().all()
            assert len(points) == 1
            assert points[0].source_type == "visual"
            assert points[0].dimension == "elbow_angle"

            # No conflicts despite the mock audio_items disagreeing — the
            # audio path never ran.
            conflicts = (
                await session.execute(
                    select(KbConflict).where(KbConflict.job_id == job_id)
                )
            ).scalars().all()
            assert conflicts == []

            # The audio_kb_extract step should be skipped (not success).
            audio_step = (
                await session.execute(
                    select(PipelineStep).where(
                        PipelineStep.job_id == job_id,
                        PipelineStep.step_type == StepType.audio_kb_extract,
                    )
                )
            ).scalar_one()
            assert audio_step.status == PipelineStepStatus.skipped
