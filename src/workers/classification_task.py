"""Celery tasks for the classification channel (Feature 013).

Tasks:
  - ``scan_cos_videos`` — COS full/incremental scan (routed to ``default`` queue).
  - ``classify_video`` — single coach video → ``tech_category`` (routed to ``classification`` queue).

Flow:
  1. Create scan record in Redis (via Celery backend) for progress tracking
  2. Create async session
  3. Call CosClassificationScanner.scan_full() or scan_incremental()
  4. Store stats in task result

The task result is a dict with ScanStats fields, retrievable via
AsyncResult(task_id).result.
"""

from __future__ import annotations

import asyncio
import logging
import time

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


def _make_session_factory():
    """Create a fresh async engine + sessionmaker for this Celery task invocation."""
    from src.config import get_settings

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def _run_scan(scan_mode: str) -> dict:
    """Execute the scan and return stats dict."""
    from src.services.cos_classification_scanner import CosClassificationScanner

    scanner = CosClassificationScanner.from_settings()
    factory = _make_session_factory()

    async with factory() as session:
        if scan_mode == "incremental":
            stats = await scanner.scan_incremental(session)
        else:
            stats = await scanner.scan_full(session)

    return {
        "scanned": stats.scanned,
        "inserted": stats.inserted,
        "updated": stats.updated,
        "skipped": stats.skipped,
        "errors": stats.errors,
        "elapsed_s": round(stats.elapsed_s, 2),
        "error_detail": stats.error_detail,
    }


@shared_task(
    bind=True,
    name="src.workers.classification_task.scan_cos_videos",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def scan_cos_videos(self, task_id: str, scan_mode: str = "full") -> dict:
    """Scan COS and classify coach videos.

    Args:
        task_id: Unique ID for this scan run (for progress tracking).
        scan_mode: 'full' or 'incremental'.

    Returns:
        dict with scanned/inserted/updated/skipped/errors/elapsed_s stats.
    """
    logger.info(
        "scan_cos_videos started: task_id=%s scan_mode=%s celery_task=%s",
        task_id, scan_mode, self.request.id,
    )
    start = time.monotonic()

    try:
        # Update task state to RUNNING with initial progress
        self.update_state(
            state="RUNNING",
            meta={
                "task_id": task_id,
                "scan_mode": scan_mode,
                "status": "running",
                "scanned": 0,
                "inserted": 0,
                "updated": 0,
                "skipped": 0,
                "errors": 0,
                "elapsed_s": 0.0,
            },
        )

        stats = asyncio.run(_run_scan(scan_mode))

        elapsed = round(time.monotonic() - start, 2)
        result = {
            "task_id": task_id,
            "scan_mode": scan_mode,
            "status": "success",
            **stats,
        }
        logger.info(
            "scan_cos_videos success: task_id=%s inserted=%d updated=%d errors=%d elapsed=%.1fs",
            task_id, stats["inserted"], stats["updated"], stats["errors"], elapsed,
        )
        return result

    except Exception as exc:
        logger.exception(
            "scan_cos_videos failed: task_id=%s error=%s", task_id, exc
        )
        elapsed = round(time.monotonic() - start, 2)
        # Retry if retries remain
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {
                "task_id": task_id,
                "scan_mode": scan_mode,
                "status": "failed",
                "scanned": 0,
                "inserted": 0,
                "updated": 0,
                "skipped": 0,
                "errors": 1,
                "elapsed_s": elapsed,
                "error_detail": str(exc),
            }


# ──────────────────────────────────────────────────────────────────────────────
# Feature 013 — classify_video: single coach-video classification
# ──────────────────────────────────────────────────────────────────────────────


async def _run_classify(task_id: str, cos_object_key: str) -> dict:
    """Delegate to ClassificationService (Phase US3 T036/T037)."""
    from datetime import datetime, timezone
    from uuid import UUID

    from sqlalchemy import select, update

    from src.models.analysis_task import AnalysisTask, TaskStatus

    factory = _make_session_factory()
    started_at = datetime.now(timezone.utc)

    async with factory() as session:
        # Mark task as processing
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == UUID(task_id))
            .values(status=TaskStatus.processing, started_at=started_at)
        )
        await session.commit()

        try:
            # Delegate to ClassificationService (T036). The service performs
            # the rule-based keyword match (LLM fallback) and upserts the
            # ``coach_video_classifications`` row; returns the assigned
            # ``tech_category``.
            from src.services.classification_service import (
                ClassificationService,
            )

            svc = ClassificationService()
            tech_category = await svc.classify_single_video(
                session=session, cos_object_key=cos_object_key
            )
            result_payload: dict = {"tech_category": tech_category}

            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.id == UUID(task_id))
                .values(
                    status=TaskStatus.success,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            return {"task_id": task_id, "status": "success", **result_payload}

        except Exception as exc:  # noqa: BLE001
            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.id == UUID(task_id))
                .values(
                    status=TaskStatus.failed,
                    completed_at=datetime.now(timezone.utc),
                    error_message=str(exc)[:2000],
                )
            )
            await session.commit()
            raise


@shared_task(
    bind=True,
    name="src.workers.classification_task.classify_video",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def classify_video(self, task_id: str, cos_object_key: str) -> dict:
    """Classify a single coach video → tech_category (Feature 013).

    Args:
        task_id: ``analysis_tasks.id`` (UUID string) enqueued by TaskSubmissionService.
        cos_object_key: full COS object key of the coach video.

    Returns:
        dict with ``task_id``, ``status``, and ``tech_category`` (when classified).
    """
    logger.info(
        "classify_video started: task_id=%s cos_object_key=%s celery_task=%s",
        task_id, cos_object_key, self.request.id,
    )
    try:
        return asyncio.run(_run_classify(task_id, cos_object_key))
    except Exception as exc:
        logger.exception("classify_video failed: task_id=%s error=%s", task_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"task_id": task_id, "status": "failed", "error": str(exc)}
