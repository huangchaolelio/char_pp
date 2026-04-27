"""Integration test — Feature 014 US5: channel compatibility (T054, T055).

Verifies (FR-015, FR-016, SC-006):
  - Submitting N kb_extraction jobs (each of which internally has 6 sub-steps)
    bumps the ``kb_extraction`` channel's ``current_processing`` / ``current_pending``
    by N — not 6N. One extraction job = one channel slot.
  - Re-running a failed job does NOT consume a fresh channel slot: the rerun
    flips an existing ``analysis_tasks`` row back to ``pending`` (same row,
    same id) and re-enqueues the same Celery task.

All done in a single test to keep the httpx client + DB session on one asyncio
loop (same pattern as the other US4/US5 integration tests in this suite).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.models.extraction_job import ExtractionJob, ExtractionJobStatus
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType


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
    """Fresh module-level engine per test — see test_rerun_us4.py::client."""
    from src.api.main import app
    from src.db import session as _session_mod

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
async def seeded_classifications(session_factory):
    """Create N classified coach videos so the kb-extraction gate passes.

    Yields a list of COS object keys. Teardown deletes everything we touched
    plus any extraction_jobs / pipeline_steps that got created mid-test.
    """
    cos_keys = [
        f"tests/f14_us5/video_{uuid.uuid4().hex[:8]}.mp4" for _ in range(3)
    ]
    async with session_factory() as session:
        for ck in cos_keys:
            session.add(
                CoachVideoClassification(
                    coach_name="US5测试教练",
                    course_series="feature014-us5",
                    cos_object_key=ck,
                    filename=ck.rsplit("/", 1)[-1],
                    tech_category="forehand_topspin",
                    tech_tags=[],
                    classification_source="rule",
                    confidence=1.0,
                    name_source="fallback",
                    kb_extracted=False,
                )
            )
        await session.commit()

    yield cos_keys

    async with session_factory() as session:
        await session.execute(
            delete(AnalysisTask).where(AnalysisTask.cos_object_key.in_(cos_keys))
        )
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key.in_(cos_keys)
            )
        )
        await session.commit()


async def _count_kb_channel(session_factory) -> dict:
    """Return ``{pending, processing}`` for the kb_extraction channel.

    We count directly in SQL so the result is independent of any caching
    that ``TaskChannelService`` may do.
    """
    async with session_factory() as session:
        pending = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(AnalysisTask)
                    .where(
                        AnalysisTask.task_type == TaskType.kb_extraction,
                        AnalysisTask.status == TaskStatus.pending,
                    )
                )
            ).scalar_one()
        )
        processing = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(AnalysisTask)
                    .where(
                        AnalysisTask.task_type == TaskType.kb_extraction,
                        AnalysisTask.status == TaskStatus.processing,
                    )
                )
            ).scalar_one()
        )
    return {"pending": pending, "processing": processing}


async def test_channel_counts_by_job_not_substeps(
    client, session_factory, seeded_classifications
) -> None:
    """T054 + T055 combined.

    - Submitting 2 jobs bumps the channel by 2 rows, not by 12 (6 sub-steps × 2).
    - A subsequent failed+rerun cycle on the same job keeps the count at 2.
    """
    initial = await _count_kb_channel(session_factory)

    # ── 1) Submit 2 kb-extraction jobs.
    submitted_task_ids: list[str] = []
    with patch(
        "src.services.task_submission_service.TaskSubmissionService._dispatch_celery",
        return_value=None,
    ):
        for ck in seeded_classifications[:2]:
            resp = await client.post(
                "/api/v1/tasks/kb-extraction",
                json={
                    "cos_object_key": ck,
                    "enable_audio_analysis": True,
                    "audio_language": "zh",
                    "force": False,
                },
            )
            assert resp.status_code == 200, resp.text
            envelope = resp.json()
            # Feature-017：POST /api/v1/tasks/kb-extraction 信封化后 body 位于 data
            assert envelope["success"] is True
            body = envelope["data"]
            assert body["accepted"] == 1
            submitted_task_ids.append(body["items"][0]["task_id"])

    # ── 2) Channel counters: exactly +2 rows, even though the DB now
    #      holds 2 extraction_jobs + 12 pipeline_steps.
    after_submit = await _count_kb_channel(session_factory)
    delta_pending = after_submit["pending"] - initial["pending"]
    delta_processing = after_submit["processing"] - initial["processing"]
    assert delta_pending + delta_processing == 2, (
        f"expected +2 analysis_tasks rows for 2 jobs, got "
        f"pending+={delta_pending} processing+={delta_processing} "
        "(channel is double-counting sub-steps)"
    )

    # Sanity: 2 jobs × 6 steps = 12 pipeline_steps landed in the DB.
    async with session_factory() as session:
        step_count = int(
            (
                await session.execute(
                    select(func.count(PipelineStep.id))
                    .join(ExtractionJob, ExtractionJob.id == PipelineStep.job_id)
                    .where(
                        ExtractionJob.cos_object_key.in_(
                            seeded_classifications[:2]
                        )
                    )
                )
            ).scalar_one()
        )
        assert step_count == 12, (
            f"expected 12 pipeline_steps for 2 jobs, got {step_count}"
        )

    # ── 3) Mark one of the jobs as failed and rerun. Count must NOT bump
    #      further — rerun reuses the same analysis_tasks row.
    task_id_to_fail = submitted_task_ids[0]
    async with session_factory() as session:
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == uuid.UUID(task_id_to_fail))
            .values(status=TaskStatus.failed)
        )
        # Locate the job.
        job_id = (
            await session.execute(
                select(AnalysisTask.extraction_job_id).where(
                    AnalysisTask.id == uuid.UUID(task_id_to_fail)
                )
            )
        ).scalar_one()
        await session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .values(
                status=ExtractionJobStatus.failed,
                intermediate_cleanup_at=datetime.now(timezone.utc)
                + timedelta(days=1),
            )
        )
        # Mark one step failed so the rerun actually resets something.
        await session.execute(
            update(PipelineStep)
            .where(
                PipelineStep.job_id == job_id,
                PipelineStep.step_type == StepType.audio_transcription,
            )
            .values(status=PipelineStepStatus.failed, error_message="simulated")
        )
        await session.commit()

    # A failed row also occupies no channel slot (pending + processing only),
    # so expected state right now: original delta is -1 in pending (job moved
    # from pending → failed), processing unchanged.
    mid = await _count_kb_channel(session_factory)

    with patch(
        "src.workers.kb_extraction_task.extract_kb.apply_async",
        return_value=None,
    ):
        resp = await client.post(
            f"/api/v1/extraction-jobs/{job_id}/rerun", json={}
        )
    assert resp.status_code == 202, resp.text

    # ── 4) After rerun, the failed row flipped back to pending — the channel
    #      gained exactly 1 slot back. No second analysis_tasks row was inserted.
    after_rerun = await _count_kb_channel(session_factory)
    assert after_rerun["pending"] == mid["pending"] + 1, (
        "rerun should flip the existing row back to pending, not create a new one"
    )
    assert after_rerun["processing"] == mid["processing"]
    # The post-rerun pending count matches the post-submit pending count (no
    # fresh slot consumed).
    assert after_rerun["pending"] == after_submit["pending"]
    assert after_rerun["processing"] == after_submit["processing"]

    # And the analysis_tasks row count for these 2 keys is still 2
    # (no extra row was inserted by the rerun).
    async with session_factory() as session:
        row_count = int(
            (
                await session.execute(
                    select(func.count(AnalysisTask.id)).where(
                        AnalysisTask.cos_object_key.in_(
                            seeded_classifications[:2]
                        )
                    )
                )
            ).scalar_one()
        )
        assert row_count == 2
