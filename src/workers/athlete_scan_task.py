"""Feature-020 — Celery task: 运动员素材扫描.

路由到 ``default`` 队列（与教练侧 scan_cos_videos 复用；队列拓扑零新增）。
失败时 retry 2 次，最终失败返回 ``error_detail``（以 ``ATHLETE_ROOT_UNREADABLE:`` 等错误码前缀起始）。

`task_type='athlete_video_classification'` 的 ``analysis_tasks`` 行由
:mod:`src.api.routers.athlete_classifications` 在触发时创建；本 task 仅执行扫描逻辑。
"""

from __future__ import annotations

import asyncio
import logging
import time

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


def _make_session_factory():
    from src.config import get_settings

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _run_scan(scan_mode: str) -> dict:
    from src.services.cos_athlete_scanner import CosAthleteScanner

    scanner = CosAthleteScanner.from_settings()
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
    name="src.workers.athlete_scan_task.scan_athlete_videos",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def scan_athlete_videos(self, task_id: str, scan_mode: str = "full") -> dict:
    """Scan COS athlete root path and classify each mp4."""
    logger.info(
        "scan_athlete_videos started: task_id=%s scan_mode=%s celery_task=%s",
        task_id, scan_mode, self.request.id,
    )
    start = time.monotonic()

    try:
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
        status = "failed" if stats.get("error_detail") else "success"
        result = {
            "task_id": task_id,
            "scan_mode": scan_mode,
            "status": status,
            **stats,
        }
        logger.info(
            "scan_athlete_videos %s: task_id=%s inserted=%d updated=%d errors=%d elapsed=%.1fs",
            status, task_id, stats["inserted"], stats["updated"], stats["errors"], elapsed,
        )
        return result

    except Exception as exc:
        logger.exception("scan_athlete_videos failed: task_id=%s error=%s", task_id, exc)
        elapsed = round(time.monotonic() - start, 2)
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
