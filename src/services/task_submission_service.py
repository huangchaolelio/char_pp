"""TaskSubmissionService — atomic task submission with DB-authoritative limiting.

Feature 013 design (see research.md R2, R4):
  - **DB is the source of truth** for channel capacity; Redis is monitoring-only.
  - Each submission transaction acquires ``pg_advisory_xact_lock(hash(task_type))``
    to serialise counter reads per channel (avoids full-table FOR UPDATE).
  - Idempotency is enforced by partial unique index
    ``idx_analysis_tasks_idempotency`` on ``(cos_object_key, task_type)``
    WHERE status IN ('pending','processing','success'); on IntegrityError the
    service surfaces ``DUPLICATE_TASK`` with the existing task_id.
  - Batch submissions apply partial-success semantics: each item is evaluated
    in order against live remaining capacity; overflow items are rejected with
    ``QUEUE_FULL`` but the request itself returns HTTP 200.
  - ``submit_batch`` enqueues successful rows via ``apply_async`` after commit —
    if enqueue fails the row stays ``pending`` and orphan recovery cleans up.

Exceptions:
  - :class:`BatchTooLargeError` — raised when ``len(items) > settings.batch_max_size``;
    router turns into 400 ``BATCH_TOO_LARGE`` (whole batch rejected).
  - :class:`ChannelDisabledError` — channel.enabled is False; 400 ``CHANNEL_DISABLED``.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from src.utils.time_utils import now_cst
from uuid import UUID, uuid4

from sqlalchemy import and_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.services.task_channel_service import ChannelLiveSnapshot, TaskChannelService

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────


class BatchTooLargeError(ValueError):
    """Raised when batch size exceeds ``settings.batch_max_size`` (400 BATCH_TOO_LARGE)."""


class ChannelDisabledError(ValueError):
    """Raised when the channel is administratively disabled (400 CHANNEL_DISABLED)."""


# ──────────────────────────────────────────────────────────────────────────────
# Feature-019 兼容层：对外仍用 "tech_category/version" 字符串，内部落库拆新列
# ──────────────────────────────────────────────────────────────────────────────


def _split_kb_version(value: str | None) -> tuple[str | None, int | None]:
    """Parse legacy ``"tech_category/version"`` into ``(kb_tech_category, kb_version)``.

    对齐 ``AnalysisTask.knowledge_base_version`` property getter 的拼接格式
    （``f"{kb_tech_category}/{kb_version}"``）。无法解析时返回 ``(None, None)``，
    由上层决定是否拒绝（当前 diagnosis 通道允许不 pin 版本）。
    """
    if not value:
        return None, None
    head, _, tail = value.partition("/")
    if not head or not tail:
        return None, None
    try:
        return head, int(tail)
    except ValueError:
        return None, None


# ──────────────────────────────────────────────────────────────────────────────
# DTOs
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class SubmissionInputItem:
    """Normalised per-item submission payload used internally by the service.

    Routers translate request-specific fields (cos_object_key / video_storage_uri
    / kb args / diagnosis args) into this shape before calling submit_batch.
    """

    # Idempotency key (COS object key for classification/kb_extraction; None for diagnosis).
    cos_object_key: str | None
    # Fields forwarded to the Celery task's ``kwargs``; router maps to the
    # right signature (``classify_video`` / ``extract_kb`` / ``diagnose_athlete``).
    task_kwargs: dict[str, Any]
    # For classification/kb_extraction: the video filename and size recorded
    # on ``analysis_tasks`` (required columns on the legacy table). Safe
    # defaults allowed when the caller doesn't have this information.
    video_filename: str = ""
    video_size_bytes: int = 0
    # video_storage_uri is required NOT NULL on analysis_tasks; for
    # classification/kb_extraction it falls back to cos_object_key.
    video_storage_uri: str | None = None
    # Optional — propagated to the row when supplied.
    coach_id: UUID | None = None
    knowledge_base_version: str | None = None
    # When True, a matching row in status=success is re-submitted (reclassify).
    force: bool = False


@dataclass(slots=True)
class SubmissionOutcome:
    """Per-item outcome returned from submit_batch."""

    index: int
    accepted: bool
    task_id: UUID | None = None
    cos_object_key: str | None = None
    rejection_code: str | None = None
    rejection_message: str | None = None
    existing_task_id: UUID | None = None


@dataclass(slots=True)
class SubmissionBatchResult:
    """Aggregate result of submit_batch; router maps to API schema."""

    task_type: TaskType
    accepted: int
    rejected: int
    items: list[SubmissionOutcome]
    channel: ChannelLiveSnapshot
    submitted_at: datetime


# ──────────────────────────────────────────────────────────────────────────────
# Service
# ──────────────────────────────────────────────────────────────────────────────


def _advisory_lock_key(task_type: TaskType) -> int:
    """Derive a stable 64-bit signed int from the task_type name for pg_advisory_xact_lock."""
    digest = hashlib.sha256(task_type.value.encode("utf-8")).digest()
    # First 8 bytes → signed int64.
    as_int = int.from_bytes(digest[:8], byteorder="big", signed=True)
    return as_int


class TaskSubmissionService:
    """Create ``analysis_tasks`` rows and enqueue Celery tasks atomically."""

    def __init__(
        self,
        channel_service: TaskChannelService | None = None,
        batch_max_size: int | None = None,
    ) -> None:
        self._channels = channel_service or TaskChannelService()
        self._batch_max = batch_max_size or get_settings().batch_max_size

    # -- public API -------------------------------------------------------

    async def submit_batch(
        self,
        session: AsyncSession,
        task_type: TaskType,
        items: Sequence[SubmissionInputItem],
        submitted_via: str = "single",
    ) -> SubmissionBatchResult:
        """Create DB rows + enqueue Celery tasks for each accepted item.

        Raises:
            BatchTooLargeError: when ``len(items) > settings.batch_max_size``.
            ChannelDisabledError: when the channel is disabled.
        """
        if len(items) == 0:
            raise ValueError("items must not be empty")
        if len(items) > self._batch_max:
            raise BatchTooLargeError(
                f"batch size {len(items)} exceeds max {self._batch_max}"
            )

        now = now_cst()
        outcomes: list[SubmissionOutcome] = []

        # Acquire channel-scoped advisory lock (released at commit/rollback).
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": _advisory_lock_key(task_type)},
        )

        cfg = await self._channels.load_config(session, task_type)
        if not cfg.enabled:
            raise ChannelDisabledError(f"channel {task_type.value} is disabled")

        # Live in-flight count for this channel (pending + processing only;
        # success/failed rows do not occupy capacity).
        from sqlalchemy import func

        inflight_q = (
            select(func.count())
            .select_from(AnalysisTask)
            .where(
                and_(
                    AnalysisTask.task_type == task_type,
                    AnalysisTask.status.in_(
                        [TaskStatus.pending, TaskStatus.processing]
                    ),
                )
            )
        )
        inflight = int((await session.execute(inflight_q)).scalar_one())
        remaining = max(0, cfg.queue_capacity - inflight)

        # Walk items, consuming remaining capacity; collect IDs to enqueue post-commit.
        to_enqueue: list[tuple[UUID, SubmissionInputItem]] = []
        for idx, item in enumerate(items):
            if remaining <= 0:
                outcomes.append(
                    SubmissionOutcome(
                        index=idx,
                        accepted=False,
                        cos_object_key=item.cos_object_key,
                        rejection_code="QUEUE_FULL",
                        rejection_message=(
                            f"channel {task_type.value} full "
                            f"({inflight}/{cfg.queue_capacity})"
                        ),
                    )
                )
                continue

            # Duplicate check (idempotency) — only meaningful when cos_object_key set.
            if item.cos_object_key:
                existing_id = await self._find_live_duplicate(
                    session, task_type, item.cos_object_key
                )
                if existing_id is not None:
                    outcomes.append(
                        SubmissionOutcome(
                            index=idx,
                            accepted=False,
                            cos_object_key=item.cos_object_key,
                            rejection_code="DUPLICATE_TASK",
                            rejection_message="task already pending/processing/success",
                            existing_task_id=existing_id,
                        )
                    )
                    continue

            # Insert the row; partial unique index catches a race.
            new_id = uuid4()
            kb_tc, kb_ver = _split_kb_version(item.knowledge_base_version)
            row = AnalysisTask(
                id=new_id,
                task_type=task_type,
                video_filename=item.video_filename or (item.cos_object_key or ""),
                video_size_bytes=item.video_size_bytes or 0,
                video_storage_uri=(
                    item.video_storage_uri or item.cos_object_key or ""
                ),
                status=TaskStatus.pending,
                cos_object_key=item.cos_object_key,
                submitted_via=submitted_via,
                coach_id=item.coach_id,
                # Feature-019: 直接写新复合列，``knowledge_base_version`` 已是只读 property
                kb_tech_category=kb_tc,
                kb_version=kb_ver,
                created_at=now,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                # Re-acquire the lock since rollback ended the transaction.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)"),
                    {"k": _advisory_lock_key(task_type)},
                )
                existing_id = await self._find_live_duplicate(
                    session, task_type, item.cos_object_key or ""
                )
                outcomes.append(
                    SubmissionOutcome(
                        index=idx,
                        accepted=False,
                        cos_object_key=item.cos_object_key,
                        rejection_code="DUPLICATE_TASK",
                        rejection_message="concurrent duplicate detected",
                        existing_task_id=existing_id,
                    )
                )
                continue

            outcomes.append(
                SubmissionOutcome(
                    index=idx,
                    accepted=True,
                    task_id=new_id,
                    cos_object_key=item.cos_object_key,
                )
            )
            to_enqueue.append((new_id, item))
            remaining -= 1

            # Feature 014: for kb_extraction, seed an ExtractionJob + 6 PipelineSteps
            # in the SAME transaction so the Celery worker can always resolve
            # ``analysis_tasks.extraction_job_id`` as soon as it picks the task up.
            if task_type == TaskType.kb_extraction:
                from src.services.kb_extraction_pipeline.orchestrator import (
                    Orchestrator as _F14Orchestrator,
                )

                await _F14Orchestrator.create_job(
                    session,
                    analysis_task_id=new_id,
                    cos_object_key=item.cos_object_key or "",
                    tech_category=(item.task_kwargs or {}).get(
                        "tech_category", "unclassified"
                    ),
                    enable_audio_analysis=bool(
                        (item.task_kwargs or {}).get("enable_audio_analysis", True)
                    ),
                    audio_language=str(
                        (item.task_kwargs or {}).get("audio_language", "zh")
                    ),
                    force=bool((item.task_kwargs or {}).get("force", False)),
                )

        await session.commit()

        # Enqueue Celery tasks AFTER commit — if a row exists but enqueue fails,
        # orphan recovery / manual re-queue handles it (no lost data).
        for task_id, item in to_enqueue:
            try:
                self._dispatch_celery(task_type, task_id, item)
            except Exception as exc:  # noqa: BLE001 — best-effort enqueue
                logger.exception(
                    "celery enqueue failed after commit: task_type=%s task_id=%s err=%s",
                    task_type.value, task_id, exc,
                )

        snapshot = await self._channels.get_snapshot(session, task_type)
        accepted = sum(1 for o in outcomes if o.accepted)
        return SubmissionBatchResult(
            task_type=task_type,
            accepted=accepted,
            rejected=len(outcomes) - accepted,
            items=outcomes,
            channel=snapshot,
            submitted_at=now,
        )

    # -- helpers ----------------------------------------------------------

    async def _find_live_duplicate(
        self,
        session: AsyncSession,
        task_type: TaskType,
        cos_object_key: str,
    ) -> UUID | None:
        if not cos_object_key:
            return None
        q = (
            select(AnalysisTask.id)
            .where(
                and_(
                    AnalysisTask.task_type == task_type,
                    AnalysisTask.cos_object_key == cos_object_key,
                    AnalysisTask.status.in_(
                        [TaskStatus.pending, TaskStatus.processing, TaskStatus.success]
                    ),
                )
            )
            .limit(1)
        )
        row = (await session.execute(q)).scalar_one_or_none()
        return row  # type: ignore[return-value]

    def _dispatch_celery(
        self,
        task_type: TaskType,
        task_id: UUID,
        item: SubmissionInputItem,
    ) -> None:
        """Send the task to the right Celery queue with the right signature."""
        # Import here to avoid circular imports at module load time.
        from src.workers.athlete_diagnosis_task import diagnose_athlete
        from src.workers.classification_task import classify_video
        from src.workers.kb_extraction_task import extract_kb

        tid = str(task_id)
        if task_type == TaskType.video_classification:
            classify_video.apply_async(
                kwargs={"task_id": tid, "cos_object_key": item.cos_object_key or ""},
                queue="classification",
            )
        elif task_type == TaskType.kb_extraction:
            extract_kb.apply_async(
                kwargs={
                    "task_id": tid,
                    "cos_object_key": item.cos_object_key or "",
                    **{
                        k: v
                        for k, v in item.task_kwargs.items()
                        if k in ("enable_audio_analysis", "audio_language")
                    },
                },
                queue="kb_extraction",
            )
        elif task_type == TaskType.athlete_diagnosis:
            diagnose_athlete.apply_async(
                kwargs={
                    "task_id": tid,
                    "video_storage_uri": item.video_storage_uri or "",
                    "knowledge_base_version": item.knowledge_base_version,
                },
                queue="diagnosis",
            )
        else:  # pragma: no cover — exhaustive
            raise ValueError(f"unknown task_type {task_type}")
