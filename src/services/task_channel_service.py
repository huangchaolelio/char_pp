"""TaskChannelService — dynamic per-task-type channel configuration + live snapshots.

Feature 013:
  - Loads ``task_channel_configs`` rows with a short TTL cache
    (``settings.channel_config_cache_ttl_s`` seconds, default 30) so that
    ``PATCH /api/v1/admin/channels/{task_type}`` updates propagate to all
    FastAPI workers within one TTL (SC-004).
  - ``get_snapshot(session, task_type)`` composes a ``ChannelSnapshot`` from
    the cached config + live ``analysis_tasks`` counts.
  - Never trusts Redis for capacity checks — the database is the single source
    of truth (see research.md R2).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.task_channel_config import TaskChannelConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChannelConfigSnapshot:
    """Plain-value cached copy of a ``task_channel_configs`` row."""

    task_type: TaskType
    queue_capacity: int
    concurrency: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class ChannelLiveSnapshot:
    """Channel config + live DB counts. Feeds the `ChannelSnapshot` schema."""

    task_type: TaskType
    queue_capacity: int
    concurrency: int
    current_pending: int
    current_processing: int
    remaining_slots: int
    enabled: bool
    recent_completion_rate_per_min: float


class TaskChannelService:
    """Per-task-type channel configuration + snapshot service."""

    # Module-level cache shared across instances within a process.
    _cache: dict[TaskType, tuple[float, ChannelConfigSnapshot]] = {}
    _cache_lock = threading.Lock()

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl = ttl_seconds if ttl_seconds is not None else get_settings().channel_config_cache_ttl_s

    # ── config loading ────────────────────────────────────────────────────
    async def load_config(
        self, session: AsyncSession, task_type: TaskType
    ) -> ChannelConfigSnapshot:
        """Return a (possibly cached) snapshot of the channel's config row."""
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(task_type)
            if cached and (now - cached[0]) < self._ttl:
                return cached[1]

        row = (
            await session.execute(
                select(TaskChannelConfig).where(TaskChannelConfig.task_type == task_type)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"no task_channel_configs row for {task_type.value}")

        snap = ChannelConfigSnapshot(
            task_type=row.task_type,
            queue_capacity=row.queue_capacity,
            concurrency=row.concurrency,
            enabled=row.enabled,
        )
        with self._cache_lock:
            self._cache[task_type] = (now, snap)
        return snap

    @classmethod
    def invalidate_cache(cls, task_type: TaskType | None = None) -> None:
        """Drop cached config(s) — call after ``PATCH /admin/channels/...``."""
        with cls._cache_lock:
            if task_type is None:
                cls._cache.clear()
            else:
                cls._cache.pop(task_type, None)

    # ── live snapshot ─────────────────────────────────────────────────────
    async def get_snapshot(
        self, session: AsyncSession, task_type: TaskType
    ) -> ChannelLiveSnapshot:
        cfg = await self.load_config(session, task_type)

        # Live counts for this channel only.
        pending_q = select(func.count()).select_from(AnalysisTask).where(
            and_(
                AnalysisTask.task_type == task_type,
                AnalysisTask.status == TaskStatus.pending,
            )
        )
        processing_q = select(func.count()).select_from(AnalysisTask).where(
            and_(
                AnalysisTask.task_type == task_type,
                AnalysisTask.status == TaskStatus.processing,
            )
        )
        current_pending = int((await session.execute(pending_q)).scalar_one())
        current_processing = int((await session.execute(processing_q)).scalar_one())

        # Rate: tasks completed (success) in the last 10 min, averaged per minute.
        from datetime import timedelta

        from src.utils.time_utils import now_cst

        window_start = now_cst() - timedelta(minutes=10)
        rate_q = (
            select(func.count())
            .select_from(AnalysisTask)
            .where(
                and_(
                    AnalysisTask.task_type == task_type,
                    AnalysisTask.status == TaskStatus.success,
                    AnalysisTask.completed_at.isnot(None),
                    AnalysisTask.completed_at >= window_start,
                )
            )
        )
        recent_completed = int((await session.execute(rate_q)).scalar_one())
        rate = round(recent_completed / 10.0, 2)

        remaining = max(0, cfg.queue_capacity - (current_pending + current_processing))
        return ChannelLiveSnapshot(
            task_type=task_type,
            queue_capacity=cfg.queue_capacity,
            concurrency=cfg.concurrency,
            current_pending=current_pending,
            current_processing=current_processing,
            remaining_slots=remaining,
            enabled=cfg.enabled,
            recent_completion_rate_per_min=rate,
        )

    async def get_all_snapshots(
        self, session: AsyncSession
    ) -> list[ChannelLiveSnapshot]:
        """Snapshot for every known channel.

        Order: classification → kb → diagnosis → preprocessing → athlete_classification → athlete_preprocessing.
        """
        return [
            await self.get_snapshot(session, tt)
            for tt in (
                TaskType.video_classification,
                TaskType.kb_extraction,
                TaskType.athlete_diagnosis,
                TaskType.video_preprocessing,
                TaskType.athlete_video_classification,
                TaskType.athlete_video_preprocessing,
            )
        ]

    # ── admin update ──────────────────────────────────────────────────────
    async def update_config(
        self,
        session: AsyncSession,
        task_type: TaskType,
        *,
        queue_capacity: int | None = None,
        concurrency: int | None = None,
        enabled: bool | None = None,
    ) -> ChannelConfigSnapshot:
        """Persist a config patch and invalidate the cache for this channel."""
        row = (
            await session.execute(
                select(TaskChannelConfig).where(TaskChannelConfig.task_type == task_type)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"no task_channel_configs row for {task_type.value}")

        if queue_capacity is not None:
            if queue_capacity <= 0:
                raise ValueError("queue_capacity must be > 0")
            row.queue_capacity = queue_capacity
        if concurrency is not None:
            if concurrency <= 0:
                raise ValueError("concurrency must be > 0")
            row.concurrency = concurrency
        if enabled is not None:
            row.enabled = enabled

        await session.commit()
        # Invalidate so the next request picks up the new row.
        self.invalidate_cache(task_type)
        logger.info(
            "task_channel_configs updated: task_type=%s capacity=%d concurrency=%d enabled=%s",
            row.task_type.value, row.queue_capacity, row.concurrency, row.enabled,
        )
        return ChannelConfigSnapshot(
            task_type=row.task_type,
            queue_capacity=row.queue_capacity,
            concurrency=row.concurrency,
            enabled=row.enabled,
        )
