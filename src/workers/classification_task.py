"""Celery task: scan COS coach video directories and classify each video.

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
