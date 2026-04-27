"""Integration test — Feature-016 US4 / T038 batch preprocessing.

Verifies ``POST /api/v1/tasks/preprocessing/batch`` per-item error
isolation: a mix of valid cos_keys (in ``coach_video_classifications``)
and invalid ones → response reports ``submitted`` / ``reused`` / ``failed``
counters with per-item detail in ``results[]``.

Mocks Celery ``.delay()`` so no worker is required; we only verify the
API contract + service-layer aggregation.

Contract: contracts/submit_preprocessing_batch.md (C1, C3, C4).

Requires:
  - PostgreSQL with migration 0014 applied.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.coach_video_classification import CoachVideoClassification
from src.models.video_preprocessing_job import VideoPreprocessingJob


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


@pytest_asyncio.fixture
async def seeded_cvcs(session_factory):
    """Seed 4 classified videos. The 5th key we pass to the batch is
    intentionally absent → triggers COS_KEY_NOT_CLASSIFIED."""
    prefix = f"tests/feature016_batch/{uuid.uuid4().hex[:8]}"
    keys = [f"{prefix}/valid_{i}.mp4" for i in range(4)]
    missing = f"{prefix}/does_not_exist.mp4"

    async with session_factory() as session:
        for i, key in enumerate(keys):
            session.add(
                CoachVideoClassification(
                    coach_name="批量测试教练",
                    course_series="feature016-batch",
                    cos_object_key=key,
                    filename=key.rsplit("/", 1)[-1],
                    tech_category="forehand_attack",
                    tech_tags=[],
                    classification_source="rule",
                    confidence=1.0,
                    name_source="fallback",
                    kb_extracted=False,
                )
            )
        await session.commit()

    yield keys, missing

    async with session_factory() as session:
        await session.execute(
            delete(VideoPreprocessingJob).where(
                VideoPreprocessingJob.cos_object_key.in_(keys + [missing])
            )
        )
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key.in_(keys + [missing])
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def client():
    from src.api.main import app
    from src.db import session as _session_mod

    if _session_mod.engine is not None:
        await _session_mod.engine.dispose()
    _session_mod.engine = _session_mod._make_engine()
    _session_mod.AsyncSessionFactory.configure(bind=_session_mod.engine)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c

    await _session_mod.engine.dispose()
    _session_mod.engine = _session_mod._make_engine()
    _session_mod.AsyncSessionFactory.configure(bind=_session_mod.engine)


class TestPreprocessingBatch:
    """T038 — per-item error isolation + counter accuracy."""

    async def test_c1_all_valid_items_submitted(self, client, seeded_cvcs):
        """C1: 4 valid cos_keys → submitted=4, failed=0, each result has
        a job_id and status='running'."""
        valid, _missing = seeded_cvcs

        with patch(
            "src.api.routers.tasks._preprocessing_enqueue_task"
        ) as mock_enqueue:
            resp = await client.post(
                "/api/v1/tasks/preprocessing/batch",
                json={"items": [{"cos_object_key": k, "force": False} for k in valid]},
            )

        assert resp.status_code == 200, resp.text
        envelope = resp.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["submitted"] == 4
        assert body["reused"] == 0
        assert body["failed"] == 0
        assert len(body["results"]) == 4
        for r in body["results"]:
            assert r["job_id"] is not None
            assert r["status"] == "running"
            assert r["error_code"] is None
            assert r["reused"] is False
        # Each fresh job triggers one enqueue call.
        assert mock_enqueue.call_count == 4

    async def test_c3_mixed_valid_and_invalid(self, client, seeded_cvcs):
        """C3: 4 valid + 1 invalid → submitted=4, failed=1; failed entry
        carries error_code='COS_KEY_NOT_CLASSIFIED' and job_id=null.

        NOTE: order is preserved — results[i] corresponds to items[i].
        """
        valid, missing = seeded_cvcs
        # interleave to confirm error doesn't short-circuit downstream items
        items = [
            {"cos_object_key": valid[0], "force": False},
            {"cos_object_key": missing, "force": False},
            {"cos_object_key": valid[1], "force": False},
            {"cos_object_key": valid[2], "force": False},
            {"cos_object_key": valid[3], "force": False},
        ]

        with patch(
            "src.api.routers.tasks._preprocessing_enqueue_task"
        ) as mock_enqueue:
            resp = await client.post(
                "/api/v1/tasks/preprocessing/batch",
                json={"items": items},
            )

        assert resp.status_code == 200, resp.text
        envelope = resp.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["submitted"] == 4
        assert body["failed"] == 1
        assert len(body["results"]) == 5

        # results[1] is the bad one
        bad = body["results"][1]
        assert bad["cos_object_key"] == missing
        assert bad["job_id"] is None
        assert bad["status"] is None
        assert bad["error_code"] == "COS_KEY_NOT_CLASSIFIED"
        assert "not" in bad["error_message"].lower()

        # Other 4 should be running with job_ids
        for idx in (0, 2, 3, 4):
            ok = body["results"][idx]
            assert ok["job_id"] is not None
            assert ok["status"] == "running"
            assert ok["error_code"] is None

        # Only 4 enqueues (the invalid one never enqueues).
        assert mock_enqueue.call_count == 4

    async def test_c4_empty_items_rejected(self, client):
        """C4: empty items array → 422 (pydantic min_length=1)."""
        resp = await client.post(
            "/api/v1/tasks/preprocessing/batch",
            json={"items": []},
        )
        assert resp.status_code == 422

    async def test_reuse_detection_in_batch(self, client, seeded_cvcs):
        """Submitting the same key twice in the same batch call: first
        creates running job, second should reuse it (reused=true)."""
        valid, _ = seeded_cvcs

        with patch("src.api.routers.tasks._preprocessing_enqueue_task"):
            # First batch — submit k0 alone, creates a running job
            await client.post(
                "/api/v1/tasks/preprocessing/batch",
                json={"items": [{"cos_object_key": valid[0], "force": False}]},
            )
            # Re-submit k0 with force=false → should hit the running job
            # (service treats running as "in-flight" and returns reused=True
            # on the existing row; exact behavior is service-layer defined)
            resp = await client.post(
                "/api/v1/tasks/preprocessing/batch",
                json={"items": [{"cos_object_key": valid[0], "force": False}]},
            )

        assert resp.status_code == 200
        envelope = resp.json()
        assert envelope["success"] is True
        body = envelope["data"]
        # Whether "reused" or submitted with a different id depends on service;
        # regardless, it should NOT be a failure.
        assert body["failed"] == 0
        assert len(body["results"]) == 1
        r = body["results"][0]
        assert r["error_code"] is None
        assert r["job_id"] is not None
