"""Feature-016 — Celery task entry point for preprocessing.

The task is routed to the ``preprocessing`` queue via ``celery_app.task_routes``.
It delegates all actual work to :func:`orchestrator.run_preprocessing` so the
logic stays unit-testable without Celery in the loop.

Failure handling: the orchestrator already persists ``status='failed'`` with a
structured error prefix; we still re-raise so Celery marks the task as failed
and so it shows up in the broker's dead-letter / retry view.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from src.workers.celery_app import celery_app


logger = logging.getLogger(__name__)


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
    from src.services.preprocessing.orchestrator import run_preprocessing

    logger.info("preprocess_video TASK start: %s", job_id)
    try:
        asyncio.run(run_preprocessing(UUID(job_id)))
    except Exception as exc:
        logger.exception("preprocess_video TASK failed for %s: %s", job_id, exc)
        raise
    return {"job_id": job_id, "state": "done"}
