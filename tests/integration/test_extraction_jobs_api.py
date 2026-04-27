"""Integration test — POST /tasks/kb-extraction creates an ExtractionJob (US1 T021 + T022).

Combined into a single test to avoid the known asyncpg + pytest-asyncio
cross-loop interaction when multiple tests share the module-level engine
from ``src/db/session.py``. The test does multiple HTTP round-trips in
sequence to exercise every endpoint once.

Requires:
  - PostgreSQL with migration 0013 applied.
  - Celery enqueue is patched out so no Celery broker is needed.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask
from src.models.coach_video_classification import CoachVideoClassification
from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=False,  # avoid cross-loop ping
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_cvc(session_factory):
    cos_key = f"tests/feature014_api/video_{uuid.uuid4().hex[:8]}.mp4"
    async with session_factory() as session:
        cvc = CoachVideoClassification(
            coach_name="API测试教练",
            course_series="feature014-api",
            cos_object_key=cos_key,
            filename=cos_key.rsplit("/", 1)[-1],
            tech_category="backhand_attack",
            tech_tags=[],
            classification_source="rule",
            confidence=1.0,
            name_source="fallback",
            kb_extracted=False,
        )
        session.add(cvc)
        await session.commit()

    yield cos_key

    async with session_factory() as session:
        await session.execute(
            delete(AnalysisTask).where(AnalysisTask.cos_object_key == cos_key)
        )
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key == cos_key
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def client():
    """An httpx AsyncClient that forces a fresh module-level engine per test.

    See the note in ``test_rerun_us4.py::client`` — asyncpg binds the
    ``src/db/session.py`` engine to the first loop that uses it, so we
    rebuild it per-test to keep the engine's loop == the test's loop.
    """
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


async def test_kb_extraction_full_api_flow(
    client, session_factory, seeded_cvc
) -> None:
    """End-to-end: submit → DB assertion → detail → list → rerun-501.

    All HTTP calls happen within a single async test to sidestep the
    cross-loop engine caching that plagues asyncpg when pytest-asyncio
    creates a fresh loop per test.
    """
    with patch(
        "src.services.task_submission_service.TaskSubmissionService._dispatch_celery",
        return_value=None,
    ):
        # 1) Submit
        resp = await client.post(
            "/api/v1/tasks/kb-extraction",
            json={
                "cos_object_key": seeded_cvc,
                "enable_audio_analysis": True,
                "audio_language": "zh",
                "force": False,
            },
        )

    assert resp.status_code == 200, resp.text
    envelope = resp.json()
    # Feature-017：POST /api/v1/tasks/kb-extraction 也已信封化
    assert envelope["success"] is True
    body = envelope["data"]
    assert body["accepted"] == 1
    task_id = body["items"][0]["task_id"]

    # 2) DB assertion — ExtractionJob + 6 PipelineSteps created in the same tx
    async with session_factory() as session:
        task = (
            await session.execute(
                select(AnalysisTask).where(AnalysisTask.id == uuid.UUID(task_id))
            )
        ).scalar_one()
        assert task.extraction_job_id is not None
        job_id = task.extraction_job_id

        job = (
            await session.execute(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
        ).scalar_one()
        assert job.tech_category == "backhand_attack"
        assert job.enable_audio_analysis is True

        steps = (
            await session.execute(
                select(PipelineStep).where(PipelineStep.job_id == job.id)
            )
        ).scalars().all()
        assert len(steps) == 6

    # 3) GET /extraction-jobs/{job_id}
    detail_resp = await client.get(f"/api/v1/extraction-jobs/{job_id}")
    assert detail_resp.status_code == 200
    detail_body = detail_resp.json()
    # Feature-017：成功信封
    assert detail_body["success"] is True
    payload = detail_body["data"]
    assert payload["job_id"] == str(job_id)
    assert payload["status"] == "pending"
    assert len(payload["steps"]) == 6
    assert payload["progress"]["total_steps"] == 6
    assert payload["progress"]["percent"] == 0.0
    step_map = {s["step_type"]: s for s in payload["steps"]}
    assert step_map["merge_kb"]["depends_on"] == [
        "visual_kb_extract",
        "audio_kb_extract",
    ]
    assert step_map["download_video"]["depends_on"] == []

    # 4) 404 on unknown id
    miss_resp = await client.get(f"/api/v1/extraction-jobs/{uuid.uuid4()}")
    assert miss_resp.status_code == 404
    miss_body = miss_resp.json()
    assert miss_body["success"] is False
    assert miss_body["error"]["code"] == "JOB_NOT_FOUND"

    # 5) GET /extraction-jobs (list)
    list_resp = await client.get("/api/v1/extraction-jobs?page=1&page_size=50")
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body["success"] is True
    # Feature-017：分页元数据统一放入 meta
    assert list_body["meta"]["page"] == 1
    assert list_body["meta"]["page_size"] == 50
    items = list_body["data"]
    assert any(it["cos_object_key"] == seeded_cvc for it in items)

    # 6) POST rerun on a pending job → 400 JOB_NOT_FAILED (US4 implemented).
    # Feature-017：状态校验类错误统一 400（章程 v1.4.0 / error-codes.md）
    rerun_resp = await client.post(
        f"/api/v1/extraction-jobs/{job_id}/rerun", json={}
    )
    assert rerun_resp.status_code == 400
    rerun_body = rerun_resp.json()
    assert rerun_body["success"] is False
    assert rerun_body["error"]["code"] == "JOB_NOT_FAILED"
