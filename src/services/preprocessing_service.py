"""Feature-016 — preprocessing_service: DB-level orchestration helpers.

Responsibilities:
- ``create_or_reuse``: idempotent job creation with ``force`` semantics.
- ``create_or_reuse_batch``: per-item isolated error capture for batch submit.
- ``mark_preprocessed``: set ``coach_video_classifications.preprocessed = true``.
- ``get_job_view``: assemble the full GET /video-preprocessing/{id} payload.

The service intentionally does NOT enqueue the Celery task itself — the
router handles that so unit tests can assert the enqueue-vs-reuse contract
without Celery broker state. See ``src.api.routers.tasks.submit_preprocessing``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.analysis_task import TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.models.task_channel_config import TaskChannelConfig
from src.models.video_preprocessing_job import (
    PreprocessingJobStatus,
    VideoPreprocessingJob,
)
from src.models.video_preprocessing_segment import VideoPreprocessingSegment
from src.services.preprocessing import cos_uploader


logger = logging.getLogger(__name__)


# ── Errors ──────────────────────────────────────────────────────────────────

class CosKeyNotClassifiedError(Exception):
    """Raised when the requested cos_object_key isn't in coach_video_classifications."""

    def __init__(self, cos_object_key: str) -> None:
        super().__init__(
            f"cos_object_key {cos_object_key!r} not found in "
            "coach_video_classifications"
        )
        self.cos_object_key = cos_object_key


class ChannelQueueFullError(Exception):
    """Raised when the preprocessing channel has no remaining slots."""

    def __init__(self, channel_name: str) -> None:
        super().__init__(f"channel {channel_name!r} queue is full")
        self.channel_name = channel_name


class BatchTooLargeError(Exception):
    def __init__(self, submitted: int, limit: int) -> None:
        super().__init__(f"batch size {submitted} exceeds limit {limit}")
        self.submitted = submitted
        self.limit = limit


# ── Return types ────────────────────────────────────────────────────────────

@dataclass
class PreprocessingCreateOutcome:
    job_id: UUID
    status: str
    reused: bool
    cos_object_key: str
    segment_count: Optional[int]
    has_audio: Optional[bool]
    started_at: datetime
    completed_at: Optional[datetime]


@dataclass
class PreprocessingBatchItemOutcome:
    cos_object_key: str
    job_id: Optional[UUID]
    status: Optional[str]
    reused: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class PreprocessingJobView:
    job_id: UUID
    cos_object_key: str
    status: str
    force: bool
    started_at: datetime
    completed_at: Optional[datetime]
    duration_ms: Optional[int]
    segment_count: Optional[int]
    has_audio: bool
    error_message: Optional[str]
    original_meta: Optional[dict]
    target_standard: Optional[dict]
    audio: Optional[dict]
    segments: list = field(default_factory=list)


# ── Helpers (module-level so tests can monkeypatch) ─────────────────────────

async def _fetch_classification(
    session: AsyncSession, cos_object_key: str
) -> Optional[CoachVideoClassification]:
    return (
        await session.execute(
            select(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key == cos_object_key
            )
        )
    ).scalar_one_or_none()


async def _fetch_success_job(
    session: AsyncSession, cos_object_key: str
) -> Optional[VideoPreprocessingJob]:
    """Return the single success job for this cos_object_key, if any."""
    return (
        await session.execute(
            select(VideoPreprocessingJob).where(
                VideoPreprocessingJob.cos_object_key == cos_object_key,
                VideoPreprocessingJob.status == PreprocessingJobStatus.success.value,
            )
        )
    ).scalar_one_or_none()


async def _fetch_channel_slot_available(session: AsyncSession) -> bool:
    """Return True if the preprocessing channel has remaining slots.

    Unlike Feature-013 channels (which count ``analysis_tasks`` rows),
    the preprocessing channel counts ``video_preprocessing_jobs.status='running'``.
    ``enabled=false`` → no slots regardless of capacity.
    """
    cfg = (
        await session.execute(
            select(TaskChannelConfig).where(
                TaskChannelConfig.task_type == TaskType.video_preprocessing
            )
        )
    ).scalar_one_or_none()
    if cfg is None or not cfg.enabled:
        return False

    running = int(
        (
            await session.execute(
                select(func.count()).select_from(VideoPreprocessingJob).where(
                    VideoPreprocessingJob.status
                    == PreprocessingJobStatus.running.value
                )
            )
        ).scalar_one()
    )
    return running < cfg.queue_capacity


def _cos_prefix_for_job(cos_object_key: str, job_id: UUID | str) -> str:
    """Return the COS prefix that holds every artefact for one job.

    Must end with a trailing slash so delete_prefix matches exactly this
    job's folder and never siblings.
    """
    return f"preprocessed/{cos_object_key}/jobs/{job_id}/"


def _outcome_from_row(
    row: VideoPreprocessingJob, *, reused: bool,
) -> PreprocessingCreateOutcome:
    return PreprocessingCreateOutcome(
        job_id=row.id,
        status=row.status,
        reused=reused,
        cos_object_key=row.cos_object_key,
        segment_count=row.segment_count,
        has_audio=row.has_audio if row.status == "success" else None,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


# ── Public API ──────────────────────────────────────────────────────────────

async def create_or_reuse(
    session: AsyncSession,
    *,
    cos_object_key: str,
    force: bool = False,
    idempotency_key: Optional[str] = None,  # Reserved for future parity with F-013.
) -> PreprocessingCreateOutcome:
    """Create a new preprocessing job (or reuse an existing success row).

    Raises:
        CosKeyNotClassifiedError: the video isn't in coach_video_classifications.
        ChannelQueueFullError: preprocessing channel has no remaining slots.
    """
    del idempotency_key  # Not used for preprocessing — partial-unique index is authoritative.

    coach = await _fetch_classification(session, cos_object_key)
    if coach is None:
        raise CosKeyNotClassifiedError(cos_object_key)

    existing = await _fetch_success_job(session, cos_object_key)
    if existing is not None and not force:
        return _outcome_from_row(existing, reused=True)

    # About to insert — re-check channel capacity.
    if not await _fetch_channel_slot_available(session):
        raise ChannelQueueFullError("preprocessing")

    # force=True + existing success → supersede the old row + purge its COS objects.
    if existing is not None and force:
        existing.status = PreprocessingJobStatus.superseded.value
        existing.completed_at = datetime.now(timezone.utc)
        session.add(existing)
        await session.flush()
        try:
            prefix = _cos_prefix_for_job(cos_object_key, existing.id)
            cos_uploader.delete_prefix(prefix)
        except Exception as exc:
            logger.warning(
                "force=true: failed to delete old COS prefix for job %s: %s",
                existing.id, exc,
            )

    new_row = VideoPreprocessingJob(
        cos_object_key=cos_object_key,
        status=PreprocessingJobStatus.running.value,
        force=force,
        started_at=datetime.now(timezone.utc),
        has_audio=False,
    )
    session.add(new_row)
    await session.flush()
    return _outcome_from_row(new_row, reused=False)


async def create_or_reuse_batch(
    session: AsyncSession,
    *,
    items: list[tuple[str, bool]],
) -> list[PreprocessingBatchItemOutcome]:
    """Per-item isolated create/reuse for batch submission.

    Args:
        items: list of (cos_object_key, force) tuples.

    Raises:
        BatchTooLargeError: total items > ``settings.batch_max_size``.
    """
    settings = get_settings()
    if len(items) > settings.batch_max_size:
        raise BatchTooLargeError(len(items), settings.batch_max_size)

    results: list[PreprocessingBatchItemOutcome] = []
    for cos_key, force in items:
        try:
            out = await create_or_reuse(
                session, cos_object_key=cos_key, force=force,
            )
            results.append(PreprocessingBatchItemOutcome(
                cos_object_key=cos_key,
                job_id=out.job_id,
                status=out.status,
                reused=out.reused,
            ))
        except CosKeyNotClassifiedError as exc:
            results.append(PreprocessingBatchItemOutcome(
                cos_object_key=cos_key, job_id=None, status=None,
                reused=False,
                error_code="COS_KEY_NOT_CLASSIFIED",
                error_message=str(exc),
            ))
        except ChannelQueueFullError as exc:
            results.append(PreprocessingBatchItemOutcome(
                cos_object_key=cos_key, job_id=None, status=None,
                reused=False,
                error_code="CHANNEL_QUEUE_FULL",
                error_message=str(exc),
            ))
    return results


async def mark_preprocessed(
    session: AsyncSession, *, cos_object_key: str,
) -> None:
    """Flip ``coach_video_classifications.preprocessed`` to True (FR-006)."""
    coach = await _fetch_classification(session, cos_object_key)
    if coach is None:
        logger.warning(
            "mark_preprocessed: no classification row for %s — skipping",
            cos_object_key,
        )
        return
    coach.preprocessed = True
    session.add(coach)


async def record_job_failed(
    session: AsyncSession, job_id: UUID, error_message: str,
) -> None:
    """Terminal failure state transition."""
    await session.execute(
        update(VideoPreprocessingJob)
        .where(VideoPreprocessingJob.id == job_id)
        .values(
            status=PreprocessingJobStatus.failed.value,
            error_message=error_message,
            completed_at=datetime.now(timezone.utc),
        )
    )


async def record_job_success(
    session: AsyncSession,
    job_id: UUID,
    *,
    duration_ms: int,
    segment_count: int,
    original_meta: dict,
    target_standard: dict,
    has_audio: bool,
    audio_cos_object_key: Optional[str],
    audio_size_bytes: Optional[int],
    local_artifact_dir: str,
) -> None:
    await session.execute(
        update(VideoPreprocessingJob)
        .where(VideoPreprocessingJob.id == job_id)
        .values(
            status=PreprocessingJobStatus.success.value,
            duration_ms=duration_ms,
            segment_count=segment_count,
            original_meta_json=original_meta,
            target_standard_json=target_standard,
            has_audio=has_audio,
            audio_cos_object_key=audio_cos_object_key,
            audio_size_bytes=audio_size_bytes,
            local_artifact_dir=local_artifact_dir,
            completed_at=datetime.now(timezone.utc),
        )
    )


async def persist_original_meta(
    session: AsyncSession,
    job_id: UUID,
    *,
    original_meta: dict,
    target_standard: dict,
    has_audio: bool,
    local_artifact_dir: str,
) -> None:
    """Write probe-stage metadata EARLY (before transcode/split), so
    ``GET /video-preprocessing/{id}`` is informative even for failed jobs.
    """
    await session.execute(
        update(VideoPreprocessingJob)
        .where(VideoPreprocessingJob.id == job_id)
        .values(
            original_meta_json=original_meta,
            target_standard_json=target_standard,
            has_audio=has_audio,
            local_artifact_dir=local_artifact_dir,
        )
    )


async def add_segment_row(
    session: AsyncSession,
    *,
    job_id: UUID,
    segment_index: int,
    start_ms: int,
    end_ms: int,
    cos_object_key: str,
    size_bytes: int,
) -> None:
    session.add(VideoPreprocessingSegment(
        job_id=job_id,
        segment_index=segment_index,
        start_ms=start_ms,
        end_ms=end_ms,
        cos_object_key=cos_object_key,
        size_bytes=size_bytes,
    ))


async def get_job_view(
    session: AsyncSession, job_id: UUID,
) -> Optional[PreprocessingJobView]:
    job = await session.get(VideoPreprocessingJob, job_id)
    if job is None:
        return None
    segs = (
        await session.execute(
            select(VideoPreprocessingSegment)
            .where(VideoPreprocessingSegment.job_id == job_id)
            .order_by(VideoPreprocessingSegment.segment_index.asc())
        )
    ).scalars().all()

    audio_view = None
    if job.has_audio and job.audio_cos_object_key and job.audio_size_bytes:
        audio_view = {
            "cos_object_key": job.audio_cos_object_key,
            "size_bytes": job.audio_size_bytes,
        }

    return PreprocessingJobView(
        job_id=job.id,
        cos_object_key=job.cos_object_key,
        status=job.status,
        force=job.force,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_ms=job.duration_ms,
        segment_count=job.segment_count,
        has_audio=job.has_audio,
        error_message=job.error_message,
        original_meta=job.original_meta_json,
        target_standard=job.target_standard_json,
        audio=audio_view,
        segments=list(segs),
    )
