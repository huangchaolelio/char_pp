"""Integration test — Feature-016 US3 / T035 observability.

Seeds preprocessing job rows in four terminal/non-terminal states
(running / success / failed / superseded) directly into the DB, then hits
``GET /api/v1/video-preprocessing/{job_id}`` and verifies the response
matches ``contracts/get_preprocessing_job.md``.

Focuses on *response assembly* (T036) and *persisted probe meta* (T037)
— i.e. the read path. No Celery, no COS, no actual pipeline work.

Requires:
  - PostgreSQL with migration 0014 applied.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from src.utils.time_utils import now_cst

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.video_preprocessing_job import VideoPreprocessingJob
from src.models.video_preprocessing_segment import VideoPreprocessingSegment


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
async def seeded_jobs(session_factory):
    """Seed 4 job rows covering all statuses.

    Returns (ids_by_status, cleanup_keys).
    """
    ids: dict[str, uuid.UUID] = {}
    test_prefix = f"tests/feature016_obs/video_{uuid.uuid4().hex[:8]}"
    now = now_cst()

    async with session_factory() as session:
        # ── success job (full metadata) ──────────────────────────────────
        success_id = uuid.uuid4()
        success_job = VideoPreprocessingJob(
            id=success_id,
            cos_object_key=f"{test_prefix}_success.mp4",
            status="success",
            force=False,
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=2),
            duration_ms=600_000,
            segment_count=4,
            has_audio=True,
            error_message=None,
            original_meta_json={
                "fps": 25.0,
                "width": 1920,
                "height": 1080,
                "duration_ms": 600_000,
                "codec": "h264",
                "size_bytes": 124_518_400,
                "has_audio": True,
            },
            target_standard_json={
                "target_fps": 30,
                "target_short_side": 720,
                "segment_duration_s": 180,
            },
            audio_cos_object_key=f"preprocessed/{test_prefix}_success/jobs/{success_id}/audio.wav",
            audio_size_bytes=19_200_000,
            local_artifact_dir=f"/tmp/coaching-advisor/jobs/preprocessing/{success_id}",
        )
        session.add(success_job)
        for idx in range(3):
            session.add(
                VideoPreprocessingSegment(
                    job_id=success_id,
                    segment_index=idx,
                    start_ms=idx * 180_000,
                    end_ms=(idx + 1) * 180_000,
                    cos_object_key=(
                        f"preprocessed/{test_prefix}_success/jobs/"
                        f"{success_id}/seg_{idx:04d}.mp4"
                    ),
                    size_bytes=22_000_000 + idx * 50_000,
                )
            )
        ids["success"] = success_id

        # ── failed job (probe meta persisted, target_standard null) ──────
        failed_id = uuid.uuid4()
        session.add(
            VideoPreprocessingJob(
                id=failed_id,
                cos_object_key=f"{test_prefix}_failed.mp4",
                status="failed",
                force=False,
                started_at=now - timedelta(minutes=5),
                completed_at=now - timedelta(minutes=4, seconds=52),
                duration_ms=None,
                segment_count=None,
                has_audio=False,
                error_message=(
                    "VIDEO_QUALITY_REJECTED: fps=12.5 below minimum 15"
                ),
                original_meta_json={
                    "fps": 12.5,
                    "width": 640,
                    "height": 480,
                    "duration_ms": 600_000,
                    "codec": "h264",
                    "size_bytes": 50_000_000,
                    "has_audio": False,
                },
                target_standard_json=None,
                audio_cos_object_key=None,
                audio_size_bytes=None,
                local_artifact_dir=None,
            )
        )
        ids["failed"] = failed_id

        # ── running job (no completed_at, no segments) ───────────────────
        running_id = uuid.uuid4()
        session.add(
            VideoPreprocessingJob(
                id=running_id,
                cos_object_key=f"{test_prefix}_running.mp4",
                status="running",
                force=False,
                started_at=now - timedelta(seconds=30),
                completed_at=None,
                has_audio=False,
            )
        )
        ids["running"] = running_id

        # ── superseded job (audit: was success, replaced by force rerun) ─
        superseded_id = uuid.uuid4()
        session.add(
            VideoPreprocessingJob(
                id=superseded_id,
                cos_object_key=f"{test_prefix}_superseded.mp4",
                status="superseded",
                force=False,
                started_at=now - timedelta(hours=2),
                completed_at=now - timedelta(hours=1, minutes=55),
                duration_ms=300_000,
                segment_count=2,
                has_audio=True,
                original_meta_json={"fps": 25.0, "width": 1280, "height": 720},
                target_standard_json={
                    "target_fps": 30,
                    "target_short_side": 720,
                    "segment_duration_s": 180,
                },
            )
        )
        ids["superseded"] = superseded_id

        await session.commit()

    yield ids

    # Teardown: cascade-delete via job (segments FK CASCADE).
    async with session_factory() as session:
        for job_id in ids.values():
            await session.execute(
                delete(VideoPreprocessingJob).where(
                    VideoPreprocessingJob.id == job_id
                )
            )
        await session.commit()


@pytest_asyncio.fixture
async def client():
    """HTTP client that rebuilds the module-level asyncpg engine per-test.

    asyncpg binds its engine to the first event loop that touches it, so
    shared ``src/db/session.py`` state from earlier tests leaks across
    loops. Rebuilding here mirrors ``test_extraction_jobs_api.py``.
    """
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


class TestPreprocessingObservability:
    """T035 — all four terminal + running states are queryable with
    contract-compliant responses."""

    async def test_c1_success_job_full_fields(self, client, seeded_jobs):
        """Contract C1: success job → 200, all fields populated, segments sorted
        （Feature-017：业务载荷位于 ``body["data"]`` 信封内）."""
        job_id = seeded_jobs["success"]
        resp = await client.get(f"/api/v1/video-preprocessing/{job_id}")
        assert resp.status_code == 200, resp.text

        envelope = resp.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["job_id"] == str(job_id)
        assert body["status"] == "success"
        assert body["force"] is False
        assert body["duration_ms"] == 600_000
        assert body["segment_count"] == 4
        assert body["has_audio"] is True
        assert body["error_message"] is None

        # original_meta + target_standard + audio all populated
        assert body["original_meta"]["fps"] == 25.0
        assert body["original_meta"]["codec"] == "h264"
        assert body["target_standard"]["target_fps"] == 30
        assert body["target_standard"]["segment_duration_s"] == 180
        assert body["audio"]["cos_object_key"].endswith("/audio.wav")
        assert body["audio"]["size_bytes"] == 19_200_000

        # segments — exactly 3 (we inserted 3), sorted by segment_index
        segs = body["segments"]
        assert len(segs) == 3
        assert [s["segment_index"] for s in segs] == [0, 1, 2]
        assert segs[0]["start_ms"] == 0
        assert segs[0]["end_ms"] == 180_000
        assert segs[1]["start_ms"] == 180_000

    async def test_c2_failed_job_has_probe_meta_but_null_target(
        self, client, seeded_jobs
    ):
        """Contract C2: failed job → error_message with structured prefix,
        target_standard/audio/segments null/empty but original_meta PRESENT
        (T037: probe persisted before downstream failure)."""
        job_id = seeded_jobs["failed"]
        resp = await client.get(f"/api/v1/video-preprocessing/{job_id}")
        assert resp.status_code == 200

        envelope = resp.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["status"] == "failed"
        assert body["error_message"].startswith("VIDEO_QUALITY_REJECTED:")
        assert body["target_standard"] is None
        assert body["audio"] is None
        assert body["segments"] == []
        # But probe meta IS persisted (T037 acceptance criterion)
        assert body["original_meta"] is not None
        assert body["original_meta"]["fps"] == 12.5

    async def test_c3_running_job_no_completion(self, client, seeded_jobs):
        """Contract C3: running job → completed_at null, segments empty."""
        job_id = seeded_jobs["running"]
        resp = await client.get(f"/api/v1/video-preprocessing/{job_id}")
        assert resp.status_code == 200

        envelope = resp.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["status"] == "running"
        assert body["completed_at"] is None
        assert body["segments"] == []
        assert body["has_audio"] is False
        assert body["audio"] is None

    async def test_c4_unknown_id_404(self, client):
        """Contract C4: non-existent UUID → 404，错误信封体."""
        resp = await client.get(f"/api/v1/video-preprocessing/{uuid.uuid4()}")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "PREPROCESSING_JOB_NOT_FOUND"

    async def test_c5_non_uuid_422(self, client):
        """Contract C5: malformed job_id → 422."""
        resp = await client.get("/api/v1/video-preprocessing/not-a-uuid")
        assert resp.status_code == 422

    async def test_c6_superseded_job_queryable(self, client, seeded_jobs):
        """Contract C6: superseded rows are still readable (audit trail)."""
        job_id = seeded_jobs["superseded"]
        resp = await client.get(f"/api/v1/video-preprocessing/{job_id}")
        assert resp.status_code == 200
        envelope = resp.json()
        assert envelope["success"] is True
        assert envelope["data"]["status"] == "superseded"

    async def test_error_message_prefix_is_greppable(
        self, client, seeded_jobs
    ):
        """T035 acceptance: ops can ``grep ^VIDEO_`` on error_message to
        classify failures without locale-dependent text parsing."""
        job_id = seeded_jobs["failed"]
        envelope = (await client.get(f"/api/v1/video-preprocessing/{job_id}")).json()
        body = envelope["data"]
        code, sep, _ = body["error_message"].partition(":")
        assert sep == ":", "error_message must use 'CODE: detail' format"
        assert code == code.upper()
        assert " " not in code  # SCREAMING_SNAKE_CASE
