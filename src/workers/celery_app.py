"""Celery application configuration.

Feature 013 — Task pipeline redesign:
- Four isolated queues: classification / kb_extraction / diagnosis / default
- Each queue is served by a dedicated worker process; crashes do not cross queues
- Static routing via ``task_routes``; submission services may use
  ``apply_async(queue=...)`` for explicit targeting when needed
"""

from celery import Celery
from celery.signals import celeryd_after_setup, worker_process_init
from kombu import Queue

from src.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()

    app = Celery(
        "coaching_advisor",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=[
            "src.workers.classification_task",
            "src.workers.kb_extraction_task",
            "src.workers.athlete_diagnosis_task",
            "src.workers.housekeeping_task",
            "src.workers.preprocessing_task",
        ],
    )

    app.conf.update(
        # Serialization
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Timeouts: SC-004 requires ≤5 min for typical classification/kb; hard=420s (7 min).
        # orphan_task_timeout_seconds (840s) = 2 x task_time_limit — used by orphan recovery.
        task_time_limit=420,
        task_soft_time_limit=360,
        # Retry policy
        task_max_retries=2,
        task_default_retry_delay=30,
        # Result expiry: 24 hours
        result_expires=86400,
        # Timezone — 整体对齐北京时间（unit: 章程 v1.4.0 / Feature-018）
        timezone="Asia/Shanghai",
        enable_utc=False,
        # Worker settings
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        # Per-task child-process recycle (treat-the-root-cause fix for the
        # 2026-04-29 kb_extraction OOM incident). Heavy ML tasks — whisper,
        # YOLOv8-pose, LLM bursts — keep allocator fragments in the prefork
        # child across invocations, so memory creeps up until the worker is
        # SIGKILL'd mid-job and leaves a zombie ``running`` row. Recycling
        # the child after every single task forces a fresh process with a
        # clean heap, GPU context, and open-fds table. Cost: +1 fork per
        # task (~50ms); benefit: no memory accumulation, no WorkerLostError.
        worker_max_tasks_per_child=1,
        # Four isolated queues — one dedicated worker per queue (see workflow.md)
        task_queues=(
            Queue("classification"),  # coach video -> tech_category  (cap 5,  conc 1)
            Queue("kb_extraction"),   # classified video -> knowledge (cap 50, conc 2)
            Queue("diagnosis"),       # athlete video -> deviations   (cap 20, conc 2)
            Queue("default"),         # scan_cos_videos + housekeeping (admin/low-priority)
            Queue("preprocessing"),   # Feature-016: coach video -> standardised segments (cap 20, conc 3)
        ),
        task_default_queue="default",
        task_routes={
            "src.workers.classification_task.classify_video": {"queue": "classification"},
            "src.workers.classification_task.scan_cos_videos": {"queue": "default"},
            "src.workers.kb_extraction_task.extract_kb": {"queue": "kb_extraction"},
            "src.workers.athlete_diagnosis_task.diagnose_athlete": {"queue": "diagnosis"},
            "src.workers.housekeeping_task.cleanup_expired_tasks": {"queue": "default"},
            "src.workers.housekeeping_task.cleanup_intermediate_artifacts": {"queue": "default"},
            "src.workers.housekeeping_task.sweep_orphan_jobs": {"queue": "default"},
            "src.workers.preprocessing_task.preprocess_video": {"queue": "preprocessing"},
        },
        # Beat schedule for data retention cleanup
        beat_schedule={
            "cleanup-expired-tasks": {
                "task": "src.workers.housekeeping_task.cleanup_expired_tasks",
                "schedule": 86400,  # daily
            },
            # Feature 014: remove local artifact dirs whose retention expired.
            "cleanup-extraction-artifacts": {
                "task": "src.workers.housekeeping_task.cleanup_intermediate_artifacts",
                "schedule": 3600,  # hourly
            },
            # Periodic orphan-recovery sweep (2026-04-29 OOM incident).
            # Worker-startup-only sweep can't free channel slots until the next
            # restart; running every 5 minutes keeps stuck ``running`` rows
            # self-healing on the same timescale as the
            # ``orphan_task_timeout_seconds`` cutoff.
            "sweep-orphan-jobs": {
                "task": "src.workers.housekeeping_task.sweep_orphan_jobs",
                "schedule": 300,  # every 5 minutes
            },
        },
    )

    return app


celery_app = create_celery_app()


@celeryd_after_setup.connect
def _sweep_orphans_on_worker_ready(sender, instance, **kwargs) -> None:  # noqa: ANN001
    """On worker startup, mark tasks stuck in `processing` past the orphan timeout as failed.

    Implementation lives in :mod:`src.workers.orphan_recovery` to avoid import cycles.
    """
    try:
        from src.workers.orphan_recovery import sweep_orphan_tasks_sync

        sweep_orphan_tasks_sync()
    except Exception as exc:  # pragma: no cover — defensive, never block worker boot
        import logging

        logging.getLogger(__name__).warning("orphan sweep on startup failed: %s", exc)


@worker_process_init.connect
def _reset_db_engine_in_forked_worker(**kwargs) -> None:  # noqa: ANN003
    """Rebuild the async DB engine inside each prefork child process.

    The module-level engine in ``src.db.session`` is created once in the
    master process (when ``celery_app.py`` is imported), and its asyncpg
    connection pool keeps Future objects bound to the master's event loop.
    When a child process later runs ``asyncio.run(...)`` inside a task,
    reusing those inherited connections triggers:

        RuntimeError: got Future <...> attached to a different loop

    The fix — mandatory for asyncpg + Celery prefork — is to discard the
    inherited pool and build a fresh engine after ``fork()``.
    """
    try:
        from src.db.session import reset_engine_for_forked_process

        reset_engine_for_forked_process()
    except Exception as exc:  # pragma: no cover — defensive
        import logging

        logging.getLogger(__name__).warning(
            "reset DB engine in forked worker failed: %s", exc
        )
