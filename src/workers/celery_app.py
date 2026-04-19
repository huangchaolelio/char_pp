"""Celery application configuration."""

from celery import Celery

from src.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()

    app = Celery(
        "coaching_advisor",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=[
            "src.workers.expert_video_task",
            "src.workers.athlete_video_task",
        ],
    )

    app.conf.update(
        # Serialization
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Timeouts: 8 minutes hard limit, 7.5 minutes soft limit
        task_time_limit=480,
        task_soft_time_limit=450,
        # Retry policy
        task_max_retries=2,
        task_default_retry_delay=30,
        # Result expiry: 24 hours
        result_expires=86400,
        # Timezone
        timezone="Asia/Shanghai",
        enable_utc=True,
        # Worker settings
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        # Beat schedule for data retention cleanup
        beat_schedule={
            "cleanup-expired-tasks": {
                "task": "src.workers.athlete_video_task.cleanup_expired_tasks",
                "schedule": 86400,  # daily
            },
        },
    )

    return app


celery_app = create_celery_app()
