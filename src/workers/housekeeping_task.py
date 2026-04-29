"""Celery housekeeping tasks — data retention cleanup (Feature 013).

Routed to the ``default`` queue and triggered by Celery beat daily.
Migrated from the legacy ``src.workers.athlete_video_task.cleanup_expired_tasks``;
the old module is deleted as part of T017.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from src.utils.time_utils import now_cst

from celery import shared_task
from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings

logger = logging.getLogger(__name__)


def _make_session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


@shared_task(
    name="src.workers.housekeeping_task.cleanup_expired_tasks",
    bind=False,
)
def cleanup_expired_tasks() -> dict:
    """Daily cleanup: physically delete expired / soft-deleted analysis tasks.

    Removes tasks where:
      - ``deleted_at IS NOT NULL`` (user explicitly deleted), OR
      - ``completed_at < NOW() - data_retention_months``

    Cascade deletes all associated data (motion analyses, deviations,
    coaching advice, expert tech points, audio transcripts, …).
    """

    async def _run_cleanup() -> int:
        settings = get_settings()
        retention_months = settings.data_retention_months
        cutoff_date = now_cst() - timedelta(days=retention_months * 30)
        factory = _make_session_factory()

        async with factory() as session:
            async with session.begin():
                from src.models.analysis_task import AnalysisTask as AT

                stmt = (
                    delete(AT)
                    .where(
                        or_(
                            AT.deleted_at.isnot(None),
                            AT.completed_at < cutoff_date,
                        )
                    )
                    .returning(AT.id)
                )
                result = await session.execute(stmt)
                return len(result.fetchall())

    count = asyncio.run(_run_cleanup())
    logger.info("Data retention cleanup: physically deleted %d expired tasks", count)
    return {"deleted_count": count}


@shared_task(
    name="src.workers.housekeeping_task.cleanup_intermediate_artifacts",
    bind=False,
)
def cleanup_intermediate_artifacts() -> dict:
    """Feature 014: remove local artifact dirs whose retention window expired.

    For each ``extraction_jobs`` row with
    ``intermediate_cleanup_at < now()``:
      - Delete ``<extraction_artifact_root>/<job_id>/`` on the local FS.
      - NULL out ``pipeline_steps.output_artifact_path`` so a later rerun
        correctly takes the force_from_scratch branch (the file is gone).
      - NULL out ``extraction_jobs.intermediate_cleanup_at`` so we don't
        process the row again.

    The ``output_summary`` JSONB is kept — it's small and useful for audits.
    """
    import shutil
    from pathlib import Path

    from sqlalchemy import update as _sql_update

    async def _run() -> dict:
        settings = get_settings()
        root = Path(settings.extraction_artifact_root)
        factory = _make_session_factory()

        now = now_cst()

        async with factory() as session:
            from src.models.extraction_job import ExtractionJob
            from src.models.pipeline_step import PipelineStep
            from sqlalchemy import select as _select

            expired = (
                await session.execute(
                    _select(ExtractionJob.id).where(
                        ExtractionJob.intermediate_cleanup_at.is_not(None),
                        ExtractionJob.intermediate_cleanup_at <= now,
                    )
                )
            ).scalars().all()

            dirs_removed = 0
            paths_cleared = 0
            for job_id in expired:
                job_dir = root / str(job_id)
                if job_dir.exists():
                    try:
                        shutil.rmtree(job_dir)
                        dirs_removed += 1
                    except OSError as exc:
                        logger.warning(
                            "cleanup_intermediate_artifacts: failed to rm %s: %s",
                            job_dir, exc,
                        )

                res = await session.execute(
                    _sql_update(PipelineStep)
                    .where(
                        PipelineStep.job_id == job_id,
                        PipelineStep.output_artifact_path.is_not(None),
                    )
                    .values(output_artifact_path=None)
                )
                paths_cleared += res.rowcount or 0

                await session.execute(
                    _sql_update(ExtractionJob)
                    .where(ExtractionJob.id == job_id)
                    .values(intermediate_cleanup_at=None)
                )

            await session.commit()
            return {
                "expired_jobs_processed": len(expired),
                "dirs_removed": dirs_removed,
                "artifact_paths_cleared": paths_cleared,
            }

    result = asyncio.run(_run())
    logger.info(
        "cleanup_intermediate_artifacts: processed=%s dirs_rm=%s paths_cleared=%s",
        result["expired_jobs_processed"],
        result["dirs_removed"],
        result["artifact_paths_cleared"],
    )

    # Feature 016 (T041): clean up preprocessing local artifact dirs.
    # ``${EXTRACTION_ARTIFACT_ROOT}/preprocessing/{pp_job_id}/`` is populated
    # by the preprocessing worker and reused by the KB extraction download
    # step (hard-link cache). Retention is decoupled from extraction jobs:
    # we key on filesystem mtime so any still-active consumer keeps it warm.
    pp_result = asyncio.run(_cleanup_preprocessing_local())
    result.update(pp_result)
    logger.info(
        "cleanup_preprocessing_local: scanned=%s dirs_rm=%s skipped_recent=%s",
        pp_result.get("preprocessing_scanned", 0),
        pp_result.get("preprocessing_dirs_removed", 0),
        pp_result.get("preprocessing_skipped_recent", 0),
    )
    return result


async def _cleanup_preprocessing_local() -> dict:
    """Feature 016 / T041 — sweep ``preprocessing/<pp_job_id>/`` dirs.

    Removal criteria (per research.md R8):
      1. Directory exists under ``${EXTRACTION_ARTIFACT_ROOT}/preprocessing/``.
      2. ``max(mtime, atime)`` of the directory > 1 hour ago. Newer dirs are
         kept — a KB extraction may still be hard-linking from them.
      3. ``mtime`` > ``preprocessing_local_retention_hours`` ago (default 24h)
         → delete the whole dir tree.

    Isolated from the Feature-015 ``<job_id>/`` KB-extraction dirs because
    we only touch the ``preprocessing/`` subtree.
    """
    import shutil
    from pathlib import Path

    settings = get_settings()
    root = Path(settings.extraction_artifact_root) / "preprocessing"
    if not root.exists():
        return {
            "preprocessing_scanned": 0,
            "preprocessing_dirs_removed": 0,
            "preprocessing_skipped_recent": 0,
        }

    now_epoch = now_cst().timestamp()
    retention_s = settings.preprocessing_local_retention_hours * 3600
    active_window_s = 3600  # 1 hour grace for in-flight consumers

    scanned = removed = skipped_recent = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        scanned += 1
        try:
            stat = entry.stat()
        except OSError:
            continue

        last_touch = max(stat.st_mtime, stat.st_atime)
        # Still within active-consumer grace window → keep.
        if now_epoch - last_touch < active_window_s:
            skipped_recent += 1
            continue

        # Outside retention window → delete.
        if now_epoch - stat.st_mtime > retention_s:
            try:
                shutil.rmtree(entry)
                removed += 1
            except OSError as exc:
                logger.warning(
                    "cleanup_preprocessing_local: failed to rm %s: %s",
                    entry, exc,
                )

    return {
        "preprocessing_scanned": scanned,
        "preprocessing_dirs_removed": removed,
        "preprocessing_skipped_recent": skipped_recent,
    }


@shared_task(
    name="src.workers.housekeeping_task.sweep_orphan_jobs",
    bind=False,
)
def sweep_orphan_jobs() -> dict:
    """Periodic orphan-recovery sweep (driven by Celery beat).

    Historically this logic only ran on worker startup via the
    ``celeryd_after_setup`` signal (:mod:`src.workers.celery_app`), which
    meant a SIGKILL'd task (e.g. OOM) could leave its ``analysis_tasks`` /
    ``extraction_jobs`` / ``pipeline_steps`` / ``video_preprocessing_jobs``
    rows stuck in ``running`` / ``processing`` until the next restart — and
    in the meantime the Feature-013 channel counter refused new submissions.

    Running the existing ``sweep_orphan_tasks_sync`` on a beat cadence
    (every 5 minutes by default) closes that gap without any new recovery
    logic — it reuses the Feature-014 / Feature-016 cascades that already
    propagate failure to dependents, free channel slots, and set
    ``error_message='orphan_recovered'``.
    """
    from src.workers.orphan_recovery import sweep_orphan_tasks_sync

    count = sweep_orphan_tasks_sync()
    logger.info("sweep_orphan_jobs: reclaimed=%s", count)
    return {"reclaimed": count}
