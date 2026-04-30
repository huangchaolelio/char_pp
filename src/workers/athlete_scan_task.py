"""Feature-020 — Celery task: 运动员素材扫描.

路由到 ``default`` 队列（与教练侧 scan_cos_videos 复用；队列拓扑零新增）。
失败时 retry 2 次，最终失败返回 ``error_detail``（以 ``ATHLETE_ROOT_UNREADABLE:`` 等错误码前缀起始）。

`task_type='athlete_video_classification'` 的 ``analysis_tasks`` 行由
:mod:`src.api.routers.athlete_classifications` 在触发时创建；本 task 负责在
开始/结束/失败时更新该行的 ``status`` / ``started_at`` / ``completed_at`` /
``error_message``，契约见 router 文件头注释
「状态权威来源：analysis_tasks 行（status / progress JSON）」。
"""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID

from celery import shared_task
from sqlalchemy import update
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


async def _mark_task_started(task_id: str) -> None:
    """将 ``analysis_tasks`` 行 pending→processing，写 started_at."""
    from src.models.analysis_task import AnalysisTask, TaskStatus
    from src.utils.time_utils import now_cst

    factory = _make_session_factory()
    async with factory() as session:
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == UUID(task_id))
            .values(status=TaskStatus.processing, started_at=now_cst())
        )
        await session.commit()


async def _mark_task_finished(task_id: str, *, success: bool, error_message: str | None = None) -> None:
    """成功→TaskStatus.success；失败→TaskStatus.failed + error_message."""
    from src.models.analysis_task import AnalysisTask, TaskStatus
    from src.utils.time_utils import now_cst

    target = TaskStatus.success if success else TaskStatus.failed
    factory = _make_session_factory()
    async with factory() as session:
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == UUID(task_id))
            .values(
                status=target,
                completed_at=now_cst(),
                error_message=(error_message or None)[:2000] if error_message else None,
            )
        )
        await session.commit()


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

    # 1) pending → processing
    try:
        asyncio.run(_mark_task_started(task_id))
    except Exception:  # noqa: BLE001
        logger.exception("scan_athlete_videos: failed to mark processing task_id=%s", task_id)

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
        had_error = bool(stats.get("error_detail"))
        status = "failed" if had_error else "success"

        # 2) processing → success/failed
        try:
            asyncio.run(
                _mark_task_finished(
                    task_id,
                    success=not had_error,
                    error_message=stats.get("error_detail"),
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "scan_athlete_videos: failed to mark terminal state task_id=%s", task_id
            )

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
            # 最终失败也要写回 failed
            try:
                asyncio.run(
                    _mark_task_finished(
                        task_id, success=False, error_message=f"max retries exceeded: {exc}"
                    )
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "scan_athlete_videos: failed to mark failed task_id=%s", task_id
                )
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
