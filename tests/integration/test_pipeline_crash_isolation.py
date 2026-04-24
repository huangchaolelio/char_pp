"""Integration test: pipeline crash isolation (Feature 013 US3 / FR-002).

Validates that if one task-type pipeline stops working (e.g. its worker is
killed or its service raises on import), the other two task-types can still
be submitted and reach ``pending`` status promptly.

Strategy (no real Celery processes required):
  * Simulate a kb_extraction outage by monkey-patching the worker's Celery
    task ``apply_async`` to raise — the submission service catches the
    dispatch failure post-commit but the DB row must still land in
    ``pending``. That mirrors what happens when the kb_extraction worker is
    killed: rows accumulate in the queue but classification / diagnosis
    are unaffected.
  * Then submit a classification task and assert it reaches ``pending``
    within the SC-001 5-second budget.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.services.task_submission_service import (
    SubmissionInputItem,
    TaskSubmissionService,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _fresh_session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_size=1, max_overflow=0, pool_pre_ping=True
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    return factory, engine


@pytest.fixture(autouse=True)
async def _cleanup():
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.cos_object_key.like("pytest/f013-t034/%")
            )
        )
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key.like("pytest/f013-t034/%")
            )
        )
        await session.commit()
    await engine.dispose()
    yield
    factory, engine = _fresh_session_factory()
    async with factory() as session:
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.cos_object_key.like("pytest/f013-t034/%")
            )
        )
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key.like("pytest/f013-t034/%")
            )
        )
        await session.commit()
    await engine.dispose()


class TestPipelineCrashIsolation:
    async def test_classification_unaffected_by_kb_dispatch_failure(self):
        """kb_extraction.apply_async raises → classification submission still succeeds in ≤5s."""
        key = "pytest/f013-t034/coach/shot_01.mp4"

        factory, engine = _fresh_session_factory()
        try:
            # Simulate the kb_extraction worker being gone: any apply_async
            # against its Celery task raises. Classification dispatch is
            # untouched, so the classification pipeline keeps working.
            import src.workers.kb_extraction_task as kb_mod

            with patch.object(
                kb_mod.extract_kb, "apply_async",
                side_effect=RuntimeError("kb_extraction worker unreachable"),
            ):
                async with factory() as session:
                    svc = TaskSubmissionService()
                    start = time.monotonic()
                    result = await svc.submit_batch(
                        session=session,
                        task_type=TaskType.video_classification,
                        items=[
                            SubmissionInputItem(
                                cos_object_key=key,
                                task_kwargs={},
                                video_filename="shot_01.mp4",
                                video_storage_uri=key,
                            )
                        ],
                        submitted_via="single",
                    )
                    elapsed = time.monotonic() - start

            assert result.accepted == 1
            assert result.rejected == 0
            assert elapsed < 5.0, f"SC-001 violated: {elapsed:.2f}s"

            async with factory() as session:
                row = (
                    await session.execute(
                        select(AnalysisTask).where(
                            AnalysisTask.cos_object_key == key
                        )
                    )
                ).scalar_one()
                assert row.task_type == TaskType.video_classification
                assert row.status == TaskStatus.pending
        finally:
            await engine.dispose()

    async def test_diagnosis_unaffected_by_classification_dispatch_failure(self):
        """classification.apply_async raises → diagnosis submission still succeeds."""
        factory, engine = _fresh_session_factory()
        try:
            import src.workers.classification_task as cls_mod

            with patch.object(
                cls_mod.classify_video, "apply_async",
                side_effect=RuntimeError("classification worker unreachable"),
            ):
                async with factory() as session:
                    svc = TaskSubmissionService()
                    start = time.monotonic()
                    result = await svc.submit_batch(
                        session=session,
                        task_type=TaskType.athlete_diagnosis,
                        items=[
                            SubmissionInputItem(
                                cos_object_key=None,
                                task_kwargs={},
                                video_filename="athlete.mp4",
                                video_storage_uri="pytest/f013-t034/athlete.mp4",
                            )
                        ],
                        submitted_via="single",
                    )
                    elapsed = time.monotonic() - start

            assert result.accepted == 1
            assert elapsed < 5.0

            async with factory() as session:
                row = (
                    await session.execute(
                        select(AnalysisTask).where(
                            AnalysisTask.video_storage_uri
                            == "pytest/f013-t034/athlete.mp4"
                        )
                    )
                ).scalar_one()
                assert row.task_type == TaskType.athlete_diagnosis
                assert row.status == TaskStatus.pending
        finally:
            await engine.dispose()
