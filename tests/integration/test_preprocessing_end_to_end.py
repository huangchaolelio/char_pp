"""Integration test — US1 end-to-end preprocessing against real COS.

Submits a preprocessing task for a short real coach video, polls the job
until success, and verifies DB + COS consistency plus the
``coach_video_classifications.preprocessed`` flag flip.

Skipped when COS credentials are not configured.

NOTE: This test runs the entire flow inside a single ``asyncio.run`` so the
asyncpg connection pool stays on one event loop. It is intentionally a
**deployment-level** test; it requires a running Celery worker subscribed
to the ``preprocessing`` queue.
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

import pytest


requires_cos = pytest.mark.skipif(
    not all(
        os.environ.get(v) for v in
        ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_BUCKET", "COS_REGION")
    )
    # Also require an explicit opt-in so routine test runs don't burn 15 min
    # waiting on a real Celery worker. Set PREPROCESSING_E2E=1 to enable.
    or os.environ.get("PREPROCESSING_E2E") != "1",
    reason=(
        "Deployment-level test — set COS_* credentials AND "
        "PREPROCESSING_E2E=1 to enable. Requires a running preprocessing worker."
    ),
)


@pytest.mark.integration
@requires_cos
class TestPreprocessingEndToEnd:
    """Deployment-only end-to-end; reads a real DB row + hits real COS."""

    def test_short_video_full_pipeline(self):
        from sqlalchemy import select
        from src.db.session import AsyncSessionFactory
        from src.models.coach_video_classification import CoachVideoClassification
        from src.models.video_preprocessing_job import VideoPreprocessingJob
        from src.models.video_preprocessing_segment import VideoPreprocessingSegment
        from src.services import preprocessing_service

        async def _run_all() -> None:
            async with AsyncSessionFactory() as s:
                row = (await s.execute(
                    select(CoachVideoClassification)
                    .where(CoachVideoClassification.tech_category != "unclassified")
                    .order_by(CoachVideoClassification.duration_s.asc().nullslast())
                    .limit(1)
                )).scalar_one_or_none()
                if row is None:
                    pytest.skip("no classified coach video seeded in DB")
                cos_key = row.cos_object_key

                out = await preprocessing_service.create_or_reuse(
                    s, cos_object_key=cos_key, force=True,
                )
                await s.commit()
                job_id: UUID = out.job_id

            # Let the worker make progress (budget = 15 min).
            final_status: str | None = None
            for _ in range(60):
                await asyncio.sleep(15)
                async with AsyncSessionFactory() as s:
                    job = await s.get(VideoPreprocessingJob, job_id)
                    if job and job.status in ("success", "failed"):
                        final_status = job.status
                        break

            assert final_status == "success", (
                f"preprocessing job {job_id} did not succeed "
                f"(final={final_status})"
            )

            async with AsyncSessionFactory() as s:
                seg_rows = (await s.execute(
                    select(VideoPreprocessingSegment)
                    .where(VideoPreprocessingSegment.job_id == job_id)
                    .order_by(VideoPreprocessingSegment.segment_index)
                )).scalars().all()
                coach_row = (await s.execute(
                    select(CoachVideoClassification)
                    .where(CoachVideoClassification.cos_object_key == cos_key)
                )).scalar_one()
            assert len(seg_rows) >= 1
            assert all(s.end_ms > s.start_ms for s in seg_rows)
            assert coach_row.preprocessed is True

        asyncio.run(_run_all())
