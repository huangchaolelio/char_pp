"""Celery task: video content curation (Feature-021).

入口：``curate_video(task_id: str, job_id: str)``
- 路由到 ``default`` 队列（与 scan / housekeeping 共用，concurrency=1）
- 每条 ``video_curation_jobs`` 行 = 一个 Celery 任务 = 一个通道槽位
- 实际工作委托给 :func:`src.services.curation.curation_service.run_curation_job`，
  Celery 任务主体只负责 ``analysis_tasks`` 行的 lifecycle 流转（pending → processing
  → success/failed）以释放 task_channel_configs 通道槽位。

失败兜底（OOM / WorkerLostError）：用 :func:`_force_fail_running_curation` 在
**独立 asyncpg 连接**上把 ``video_curation_jobs.status='running'`` + 关联
``analysis_tasks.status='processing'`` 强制翻成 ``failed``，与 Feature-014 的
``kb_extraction_task._force_fail_running_job`` 同惯例。
"""

from __future__ import annotations

import asyncio
import logging
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


def _force_fail_running_curation(task_id: str, error_message: str) -> None:
    """Best-effort 同步回滚清洗作业 + 关联 analysis_tasks 行的状态。

    走 asyncpg 直连，独立于（可能损坏的）SQLAlchemy 引擎。永不抛异常。
    """
    try:
        import asyncpg

        from src.config import get_settings

        settings = get_settings()
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

        async def _run() -> None:
            conn = await asyncpg.connect(dsn)
            try:
                async with conn.transaction():
                    # video_curation_jobs（通过 cos_object_key 关联当前 task）
                    cos_key = await conn.fetchval(
                        "SELECT cos_object_key FROM analysis_tasks WHERE id = $1::uuid",
                        task_id,
                    )
                    if cos_key:
                        await conn.execute(
                            """
                            UPDATE video_curation_jobs
                               SET status = 'failed',
                                   error_code = COALESCE(NULLIF(error_code, ''), 'CURATION_TIMEOUT'),
                                   error_message = COALESCE(NULLIF(error_message, ''), $2),
                                   completed_at = NOW(),
                                   updated_at = NOW()
                             WHERE cos_object_key = $1
                               AND status = 'running'
                            """,
                            cos_key,
                            error_message,
                        )
                    await conn.execute(
                        """
                        UPDATE analysis_tasks
                           SET status = 'failed',
                               error_message = COALESCE(NULLIF(error_message, ''), $2),
                               completed_at = NOW()
                         WHERE id = $1::uuid
                           AND status = 'processing'
                        """,
                        task_id,
                        error_message,
                    )
            finally:
                await conn.close()

        asyncio.run(_run())
    except Exception:
        logger.exception("force_fail_running_curation: rollback failed for %s", task_id)


async def _run_curate(task_id: str, job_id: str) -> dict:
    """驱动 curation_service.run_curation_job 并同步 analysis_tasks lifecycle."""
    from src.models.analysis_task import AnalysisTask, TaskStatus
    from src.services.curation.curation_service import run_curation_job
    from src.utils.time_utils import now_cst

    factory = _make_session_factory()
    async with factory() as session:
        # 1) analysis_tasks → processing
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == UUID(task_id))
            .values(status=TaskStatus.processing, started_at=now_cst())
        )
        await session.commit()

        # 2) 跑清洗
        try:
            final = await run_curation_job(session, UUID(job_id))
        except Exception as exc:  # noqa: BLE001
            logger.exception("run_curation_job crashed: job_id=%s err=%s", job_id, exc)
            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.id == UUID(task_id))
                .values(
                    status=TaskStatus.failed,
                    completed_at=now_cst(),
                    error_message=str(exc)[:2000],
                )
            )
            await session.commit()
            raise

        # 3) 镜像 video_curation_jobs.status 到 analysis_tasks.status
        parent_status = (
            TaskStatus.success if final == "success" else TaskStatus.failed
        )
        await session.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == UUID(task_id))
            .values(status=parent_status, completed_at=now_cst())
        )
        await session.commit()

        return {"task_id": task_id, "job_id": job_id, "status": final}


@shared_task(
    bind=True,
    name="src.workers.curation_task.curate_video",
    max_retries=0,  # 重试由 service 内部决策；Celery 层不自动重试
    acks_late=True,
    soft_time_limit=600,  # = CURATION_JOB_TIMEOUT_SECONDS（默认 600）
    time_limit=620,
)
def curate_video(self, task_id: str, job_id: str) -> dict:
    """对单条已分类已预处理视频跑内容清洗.

    Pre-conditions（由 :func:`submit_curation` 在排队前保证）:
      - ``video_curation_jobs`` 行存在 ``status='pending'``
      - 关联 ``analysis_tasks`` 行 ``task_type=video_curation`` ``status=pending``
      - ``preprocessing_job_id`` 关联的 segments 已就绪
    """
    logger.info(
        "curate_video started: task_id=%s job_id=%s celery_task=%s",
        task_id, job_id, self.request.id,
    )

    # 与 kb_extraction_task / preprocessing_task 同惯例：fork 后重置 DB engine
    try:
        from src.db.session import reset_engine_for_forked_process

        reset_engine_for_forked_process()
    except Exception:
        logger.exception("curate_video: failed to reset DB engine, continuing")

    try:
        return asyncio.run(_run_curate(task_id, job_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("curate_video failed: task_id=%s err=%s", task_id, exc)
        _force_fail_running_curation(
            task_id,
            f"CURATION_FAILED: task crashed — {type(exc).__name__}: {exc}",
        )
        return {"task_id": task_id, "status": "failed", "error": str(exc)[:2000]}
