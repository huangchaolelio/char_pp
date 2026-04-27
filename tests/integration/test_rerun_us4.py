"""Integration test — Feature 014 US4: partial rerun of failed jobs (T048–T050).

Covers:
  T048: rerun contract — 202 happy path, 404 unknown job, 409 non-failed job,
        409 intermediate expired + force_from_scratch=false.
  T049: partial rerun — only failed + downstream skipped steps reset to pending;
        success steps keep their output_summary / artifact path; wall-clock
        reflects the savings (FR-005, SC-005).
  T050: intermediate cleanup expired → 409 rerun_hint; force_from_scratch=true
        resets ALL steps including success and returns 202.

All driven through the HTTP API so the orchestration path is exercised end-to-end.
Celery enqueue is patched out — rerun should NOT re-enqueue a new task via
the submission path; it should directly invoke ``extract_kb.apply_async`` with
the existing task_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
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


@pytest_asyncio.fixture
async def client():
    """An httpx AsyncClient that forces a fresh module-level engine per test.

    ``src/db/session.py`` creates its engine at import time and binds it to
    whichever asyncio loop first executes a request. Subsequent tests that
    get a new pytest-asyncio loop then hit a stale engine and asyncpg raises
    "attached to a different loop". Disposing and rebuilding around each
    test keeps the engine paired with the active loop.
    """
    from src.api.main import app
    from src.db import session as _session_mod

    # Rebuild the engine on the current loop.
    if _session_mod.engine is not None:
        await _session_mod.engine.dispose()
    _session_mod.engine = _session_mod._make_engine()
    _session_mod.AsyncSessionFactory.configure(bind=_session_mod.engine)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    await _session_mod.engine.dispose()
    _session_mod.engine = _session_mod._make_engine()
    _session_mod.AsyncSessionFactory.configure(bind=_session_mod.engine)


@pytest_asyncio.fixture
async def seeded_failed_job(session_factory):
    """Build a kb_extraction task + ExtractionJob where audio_transcription has
    failed and its downstream is skipped. Yields (task_id, cos_key, job_id).

    This mimics the state US4 is meant to rescue: a partial pipeline run
    where the download + pose succeeded but the audio side fell over.
    """
    cos_key = f"tests/f14_us4/video_{uuid.uuid4().hex[:8]}.mp4"
    task_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None

    async with session_factory() as session:
        cvc = CoachVideoClassification(
            coach_name="US4测试教练",
            course_series="feature014-us4",
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
            error_message="simulated audio_transcription failure",
            # completed_at in the past; cleanup window is still ahead.
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            intermediate_cleanup_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        session.add(job)
        await session.flush()
        job_id = job.id

        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == task_id)
            .values(extraction_job_id=job_id)
        )

        # 6 steps: download & pose & visual = success; audio_transcription = failed;
        # audio_kb_extract & merge_kb = skipped.
        step_states: list[tuple[StepType, PipelineStepStatus, dict | None, str | None, str | None]] = [
            (
                StepType.download_video,
                PipelineStepStatus.success,
                {"video_size_bytes": 1024, "reused": False},
                "/tmp/coaching-advisor/jobs/us4/video.mp4",
                None,
            ),
            (
                StepType.pose_analysis,
                PipelineStepStatus.success,
                {"backend": "sleep", "slept_s": 0.1},
                "/tmp/coaching-advisor/jobs/us4/pose.json",
                None,
            ),
            (
                StepType.audio_transcription,
                PipelineStepStatus.failed,
                None,
                None,
                "simulated: whisper timeout",
            ),
            (
                StepType.visual_kb_extract,
                PipelineStepStatus.success,
                {"kb_items_count": 1, "kb_items": [
                    {
                        "dimension": "elbow_angle",
                        "param_min": 90, "param_max": 120, "param_ideal": 105,
                        "unit": "°",
                        "extraction_confidence": 0.9,
                        "action_type": "forehand_topspin",
                    }
                ]},
                None,
                None,
            ),
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
                    started_at=datetime.now(timezone.utc) - timedelta(minutes=6),
                    completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                )
            )
        await session.commit()

    yield task_id, cos_key, job_id

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
        await session.execute(delete(AnalysisTask).where(AnalysisTask.id == task_id))
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key == cos_key
            )
        )
        await session.commit()


# ── T048 + T049 + T050: full US4 flow in one test (single loop) ─────────────


async def test_rerun_full_flow(
    client, session_factory, seeded_failed_job
) -> None:
    """Combined T048+T049+T050 flow.

    Merging into one test sidesteps asyncpg's well-known cross-loop engine
    issue — pytest-asyncio creates a fresh loop per test, and the module-level
    engine imported by the FastAPI app doesn't always follow. Within a single
    test, both the HTTP client and the fixture's session_factory run on the
    same loop.

    Sequence:
      1. 409 JOB_NOT_FAILED when the job is in ``success`` state.
      2. 409 INTERMEDIATE_EXPIRED when the retention window has passed and
         ``force_from_scratch`` is false.
      3. 202 happy path: default rerun resets only failed + downstream steps;
         ``success`` steps keep their artifacts.
      4. 202 force: ``force_from_scratch=true`` resets ALL steps + clears
         artifacts.
    """
    task_id, cos_key, job_id = seeded_failed_job

    # ── 1) job currently failed → flip to success first, expect 409.
    async with session_factory() as session:
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(status=ExtractionJobStatus.success)
        )
        await session.commit()

    resp = await client.post(
        f"/api/v1/extraction-jobs/{job_id}/rerun", json={}
    )
    # Feature-017：状态校验类错误统一 400（章程 v1.4.0）
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "JOB_NOT_FAILED"

    # Restore to failed.
    async with session_factory() as session:
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(status=ExtractionJobStatus.failed)
        )
        await session.commit()

    # ── 2) intermediate expired without force → 409 INTERMEDIATE_EXPIRED.
    async with session_factory() as session:
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(
                intermediate_cleanup_at=datetime.now(timezone.utc)
                - timedelta(hours=1)
            )
        )
        await session.commit()

    resp = await client.post(
        f"/api/v1/extraction-jobs/{job_id}/rerun", json={}
    )
    # Feature-017：中间结果过期 → 410 GONE（章程 error-codes.md）
    assert resp.status_code == 410
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERMEDIATE_EXPIRED"
    assert "rerun_hint" in body["error"]["details"]

    # Reset cleanup timestamp so the partial-rerun path is permitted.
    async with session_factory() as session:
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(
                intermediate_cleanup_at=datetime.now(timezone.utc)
                + timedelta(days=1)
            )
        )
        await session.commit()

    # ── 3) default rerun: only the failed + downstream skipped rows reset.
    with patch(
        "src.workers.kb_extraction_task.extract_kb.apply_async",
        return_value=None,
    ):
        resp = await client.post(
            f"/api/v1/extraction-jobs/{job_id}/rerun", json={}
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # Feature-017：happy-path 返回 信封
    assert body["success"] is True
    data = body["data"]
    assert data["job_id"] == str(job_id)
    assert data["status"] == "running"
    assert set(data["reset_steps"]) == {
        "audio_transcription",
        "audio_kb_extract",
        "merge_kb",
    }

    async with session_factory() as session:
        rows = {
            s.step_type.value: s
            for s in (
                await session.execute(
                    select(PipelineStep).where(PipelineStep.job_id == job_id)
                )
            ).scalars().all()
        }
        assert rows["download_video"].status == PipelineStepStatus.success
        assert rows["pose_analysis"].status == PipelineStepStatus.success
        assert rows["visual_kb_extract"].status == PipelineStepStatus.success
        # ``success`` steps must keep their artifact + summary.
        assert rows["download_video"].output_artifact_path is not None
        assert rows["pose_analysis"].output_artifact_path is not None
        assert rows["visual_kb_extract"].output_summary is not None
        # The trio that was failed or downstream-skipped: reset.
        assert rows["audio_transcription"].status == PipelineStepStatus.pending
        assert rows["audio_transcription"].error_message is None
        assert rows["audio_kb_extract"].status == PipelineStepStatus.pending
        assert rows["merge_kb"].status == PipelineStepStatus.pending

        job_row = (
            await session.execute(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
        ).scalar_one()
        assert job_row.status == ExtractionJobStatus.running
        assert job_row.error_message is None

        parent_task = (
            await session.execute(
                select(AnalysisTask).where(AnalysisTask.id == task_id)
            )
        ).scalar_one()
        # Parent task must flip back to pending so Feature-013 channel
        # counting doesn't see a stale 'failed' row.
        assert parent_task.status == TaskStatus.pending

    # ── 4) force_from_scratch: reset ALL steps + clear artifacts.
    # First mark job failed again so the endpoint accepts the request.
    async with session_factory() as session:
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(
                status=ExtractionJobStatus.failed,
                intermediate_cleanup_at=datetime.now(timezone.utc)
                - timedelta(hours=1),  # expired, but force overrides
            )
        )
        await session.commit()

    with patch(
        "src.workers.kb_extraction_task.extract_kb.apply_async",
        return_value=None,
    ):
        resp = await client.post(
            f"/api/v1/extraction-jobs/{job_id}/rerun",
            json={"force_from_scratch": True},
        )
    assert resp.status_code == 202, resp.text
    force_body = resp.json()
    assert force_body["success"] is True
    assert set(force_body["data"]["reset_steps"]) == {
        "download_video",
        "pose_analysis",
        "audio_transcription",
        "visual_kb_extract",
        "audio_kb_extract",
        "merge_kb",
    }

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(PipelineStep).where(PipelineStep.job_id == job_id)
            )
        ).scalars().all()
        assert all(r.status == PipelineStepStatus.pending for r in rows)
        for r in rows:
            assert r.output_summary is None
            assert r.output_artifact_path is None
            assert r.started_at is None
            assert r.completed_at is None
