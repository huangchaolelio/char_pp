"""Feature-016 — Celery task entry point for preprocessing.

The task is routed to the ``preprocessing`` queue via ``celery_app.task_routes``.
It delegates all actual work to :func:`orchestrator.run_preprocessing` so the
logic stays unit-testable without Celery in the loop.

Failure handling: the orchestrator already persists ``status='failed'`` with a
structured error prefix; we still re-raise so Celery marks the task as failed
and so it shows up in the broker's dead-letter / retry view.

Event-loop hygiene (treat-the-root-cause fix for the "10 jobs stuck in
running" incident): Celery prefork workers reuse child processes for many
tasks, but each task opens a brand-new ``asyncio.run(...)`` loop. The
module-level SQLAlchemy async engine caches asyncpg connections that are
bound to the *first* loop that ever touched the pool; from the 2nd task
onwards the child raises ``RuntimeError: got Future attached to a different
loop``. We therefore rebuild the engine at the start of *every* task — not
just once per fork — and, as a belt-and-braces guarantee, we always roll
the DB row back from ``running`` to ``failed`` via a synchronous psycopg-ish
path that does not depend on the async engine at all.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _force_fail_running_job(job_id: str, error_message: str) -> None:
    """Best-effort synchronous rollback of ``running`` → ``failed``.

    Runs in a fresh short-lived connection that is *independent* of the
    broken async engine. Never raises — we don't want bookkeeping errors
    to mask the real task failure.
    """
    try:
        import asyncpg

        from src.config import get_settings

        settings = get_settings()
        # asyncpg needs a plain DSN, not the SQLAlchemy ``+asyncpg`` variant.
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

        async def _run() -> None:
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute(
                    """
                    UPDATE video_preprocessing_jobs
                       SET status = 'failed',
                           error_message = COALESCE(NULLIF(error_message, ''), $2),
                           completed_at = NOW(),
                           updated_at = NOW()
                     WHERE id = $1::uuid
                       AND status = 'running'
                    """,
                    job_id,
                    error_message,
                )
            finally:
                await conn.close()

        asyncio.run(_run())
    except Exception:
        logger.exception(
            "force_fail_running_job: failed to roll back %s to failed", job_id
        )


@celery_app.task(
    name="src.workers.preprocessing_task.preprocess_video",
    bind=True,
)
def preprocess_video(self, job_id: str) -> dict[str, str]:
    """Run the full preprocessing pipeline for a ``video_preprocessing_jobs`` row.

    Args:
        job_id: UUID of the row as a string (JSON-serialised by the broker).

    Returns:
        ``{"job_id": ..., "state": "done"}`` — Celery stores this as the task result.
    """
    # ── Step 0: per-task engine reset ──────────────────────────────────────
    # Prefork child processes are reused across tasks, but each task opens a
    # new event loop via ``asyncio.run``. The module-level engine in
    # ``src.db.session`` caches asyncpg connections bound to the *previous*
    # task's loop; reusing them raises
    #   ``RuntimeError: got Future attached to a different loop``
    # which in turn leaves the DB row stuck in ``status='running'``. We
    # rebuild the engine here — cheap and idempotent — to guarantee a clean
    # slate for every single task dispatch.
    try:
        from src.db.session import reset_engine_for_forked_process

        reset_engine_for_forked_process()
    except Exception:
        logger.exception("preprocess_video: failed to reset DB engine, continuing")

    from src.services.preprocessing.orchestrator import run_preprocessing

    logger.info("preprocess_video TASK start: %s", job_id)
    try:
        asyncio.run(run_preprocessing(UUID(job_id)))
    except Exception as exc:
        logger.exception("preprocess_video TASK failed for %s: %s", job_id, exc)
        # Belt-and-braces: orchestrator's own try/except can fail to reach
        # the DB (e.g. when the crash is at the event-loop level, such as
        # "Future attached to a different loop"). Roll the row to ``failed``
        # on a fresh connection so it never stays orphaned in ``running``.
        _force_fail_running_job(
            job_id,
            f"VIDEO_TRANSCODE_FAILED: task crashed — {type(exc).__name__}: {exc}",
        )
        raise
    return {"job_id": job_id, "state": "done"}
