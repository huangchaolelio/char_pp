"""Celery task: process a professional coach video to build a knowledge base draft.

Flow (T024):
  1. Mark task as processing
  2. Verify COS object exists -> fail with COS_OBJECT_NOT_FOUND if missing
  3. Download to local temp dir via cos_client
  4. Validate video quality -> reject if below threshold
  5. Run MediaPipe pose estimation on every frame
  6. Segment frames into discrete action clips (wrist-velocity peaks)
  7. Classify each segment (forehand_topspin / backhand_push / unknown)
  8. Extract technical dimensions per classified segment
  9. Persist ExpertTechPoints + create draft TechKnowledgeBase version in one DB transaction
 10. Clean up local temp file
 11. AnalysisTask.status = success (set inside the same transaction as step 9)

Error handling:
  - VideoQualityRejected   -> status=rejected, rejection_reason set, temp cleaned
  - CosObjectNotFoundError -> status=failed,   error_message=COS_OBJECT_NOT_FOUND
  - CosDownloadError       -> status=failed,   error_message=COS_DOWNLOAD_FAILED
  - Any other exception    -> status=failed,   error_message set, task retried (max 2x)
"""

from __future__ import annotations


import asyncio
import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from celery import shared_task

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus
from src.services import (
    action_classifier,
    action_segmenter,
    cos_client,
    knowledge_base_svc,
    pose_estimator,
    tech_extractor,
    video_validator,
)
from src.services.cos_client import CosDownloadError, CosObjectNotFoundError
from src.services.video_validator import VideoMeta, VideoQualityRejected

logger = logging.getLogger(__name__)


def _make_session_factory():
    """Create a fresh async engine + sessionmaker for each Celery task invocation.

    Celery forks new processes, so we must NOT reuse the module-level engine
    (which is bound to the parent's event loop). Creating a fresh engine here
    ensures asyncpg connects on the current event loop.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    settings = get_settings()
    _engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _set_processing(task_id: uuid.UUID) -> None:
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.status = TaskStatus.processing
                task.started_at = datetime.now(tz=timezone.utc)


async def _set_rejected(task_id: uuid.UUID, reason: str) -> None:
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.status = TaskStatus.rejected
                task.rejection_reason = reason
                task.completed_at = datetime.now(tz=timezone.utc)


async def _set_failed(task_id: uuid.UUID, error_message: str) -> None:
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.status = TaskStatus.failed
                task.error_message = error_message
                task.completed_at = datetime.now(tz=timezone.utc)


async def _persist_success(
    task_id: uuid.UUID,
    video_meta: VideoMeta,
    extraction_results: list,
) -> str:
    """Create draft KB, save tech points, and mark task success — all in one transaction.

    Returns the newly created KB version string.
    """
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            # Derive action types covered from extracted results (fall back to v1 defaults)
            action_types = list({r.action_type for r in extraction_results}) or [
                "forehand_topspin",
                "backhand_push",
            ]

            kb = await knowledge_base_svc.create_draft_version(
                session,
                action_types=action_types,
                notes=f"Auto-extracted from task {task_id}",
            )
            kb_version = kb.version

            if extraction_results:
                count = await knowledge_base_svc.add_tech_points(
                    session,
                    kb_version=kb_version,
                    source_task_id=task_id,
                    extraction_results=extraction_results,
                )
                logger.info(
                    "Saved %d tech points to KB draft %s (task %s)",
                    count, kb_version, task_id,
                )

            # Update AnalysisTask fields in the same transaction
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.status = TaskStatus.success
                task.completed_at = datetime.now(tz=timezone.utc)
                task.video_fps = video_meta.fps
                task.video_resolution = video_meta.resolution_str
                task.video_duration_seconds = video_meta.duration_seconds
                task.knowledge_base_version = kb_version

    return kb_version


# ── Cleanup helper ─────────────────────────────────────────────────────────────

def _cleanup(tmp_path: Path | None) -> None:
    """Best-effort temp file cleanup — never raises."""
    if tmp_path is not None:
        try:
            cos_client.cleanup_temp_file(tmp_path)
        except Exception:  # noqa: BLE001
            logger.debug("Temp file cleanup skipped (non-fatal): %s", tmp_path)


# ── Celery task ────────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name="src.workers.expert_video_task.process_expert_video",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def process_expert_video(
    self,
    task_id_str: str,
    cos_object_key: str,
) -> dict:
    """Process a professional coach video and create a draft knowledge base version.

    Args:
        task_id_str: String representation of the AnalysisTask UUID.
        cos_object_key: COS object key for the video file.

    Returns:
        Dict with task_id, status, and on success: kb_version_draft, extracted_segments.
    """
    task_id = uuid.UUID(task_id_str)
    tmp_path: Path | None = None

    logger.info("expert_video task started | task_id=%s cos_key=%s", task_id, cos_object_key)

    try:
        # ── 1. Mark as processing ──────────────────────────────────────────────
        asyncio.run(_set_processing(task_id))

        # ── 2. Verify COS object exists ────────────────────────────────────────
        if not cos_client.object_exists(cos_object_key):
            logger.warning("COS object missing: %s (task %s)", cos_object_key, task_id)
            asyncio.run(_set_failed(task_id, "COS_OBJECT_NOT_FOUND"))
            return {"task_id": task_id_str, "status": "failed", "error": "COS_OBJECT_NOT_FOUND"}

        # ── 3. Download to temp dir ────────────────────────────────────────────
        try:
            tmp_path = cos_client.download_to_temp(cos_object_key)
        except CosObjectNotFoundError:
            asyncio.run(_set_failed(task_id, "COS_OBJECT_NOT_FOUND"))
            return {"task_id": task_id_str, "status": "failed", "error": "COS_OBJECT_NOT_FOUND"}
        except CosDownloadError as exc:
            asyncio.run(_set_failed(task_id, f"COS_DOWNLOAD_FAILED: {exc.reason}"))
            return {"task_id": task_id_str, "status": "failed", "error": "COS_DOWNLOAD_FAILED"}

        # ── 4. Validate video quality ──────────────────────────────────────────
        try:
            video_meta = video_validator.validate_video(tmp_path)
            logger.info(
                "Video validated: fps=%.1f res=%s dur=%.1fs (task %s)",
                video_meta.fps, video_meta.resolution_str, video_meta.duration_seconds, task_id,
            )
        except VideoQualityRejected as exc:
            _cleanup(tmp_path)
            tmp_path = None
            asyncio.run(_set_rejected(task_id, f"{exc.reason}: {exc.details}"))
            return {
                "task_id": task_id_str,
                "status": "rejected",
                "reason": exc.reason,
                "details": exc.details,
            }

        # ── 4.5 Clip to 60s + downscale to 720p (OOM guard for large videos) ──
        clipped_path = tmp_path.parent / f"clip_{tmp_path.stem}.mp4"
        ffmpeg_bin = shutil.which("ffmpeg") or "/opt/conda/bin/ffmpeg"
        clip_cmd = [
            ffmpeg_bin, "-y", "-t", "60",
            "-i", str(tmp_path),
            "-vf", "scale=1280:720",
            "-c:v", "libx264", "-crf", "23", "-an",
            str(clipped_path),
        ]
        result = subprocess.run(clip_cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and clipped_path.exists():
            _cleanup(tmp_path)
            tmp_path = clipped_path
            logger.info("Clipped to 60s/720p: %s (%.1f KB, task %s)",
                        tmp_path, tmp_path.stat().st_size / 1024, task_id)
        else:
            logger.warning("ffmpeg clip failed (rc=%d), using original (task %s)",
                           result.returncode, task_id)

        # ── 5. Pose estimation ─────────────────────────────────────────────────
        logger.info("Running pose estimation (task %s)…", task_id)
        all_frames = pose_estimator.estimate_pose(tmp_path)

        if not all_frames:
            _cleanup(tmp_path)
            tmp_path = None
            asyncio.run(_set_failed(
                task_id, "NO_MOTION_DETECTED: pose estimation returned no frames"
            ))
            return {"task_id": task_id_str, "status": "failed", "error": "NO_MOTION_DETECTED"}

        # ── 6. Segment actions ─────────────────────────────────────────────────
        segments = action_segmenter.segment_actions(all_frames)
        logger.info("%d action segments detected (task %s)", len(segments), task_id)

        # ── 7. Classify segments ───────────────────────────────────────────────
        classified_segments = []
        for seg in segments:
            seg_frames = action_segmenter.frames_for_segment(all_frames, seg)
            classified = action_classifier.classify_segment(seg_frames, seg)
            classified_segments.append(classified)

        known_segments = [cs for cs in classified_segments if cs.action_type != "unknown"]
        logger.info(
            "%d/%d segments classified as known action type (task %s)",
            len(known_segments), len(classified_segments), task_id,
        )

        # ── 8. Extract technical dimensions ───────────────────────────────────
        extraction_results = []
        for cs in known_segments:
            result = tech_extractor.extract_tech_points(cs, all_frames)
            if result.dimensions:
                extraction_results.append(result)
                logger.debug(
                    "Segment [%dms–%dms] %s → %d dims (task %s)",
                    cs.segment.start_ms, cs.segment.end_ms,
                    cs.action_type, len(result.dimensions), task_id,
                )

        logger.info(
            "%d segments yielded extractable tech points (task %s)",
            len(extraction_results), task_id,
        )

        # ── 9. Persist to DB (single transaction) ─────────────────────────────
        kb_version = asyncio.run(_persist_success(task_id, video_meta, extraction_results))

        # ── 10. Clean up temp file ─────────────────────────────────────────────
        _cleanup(tmp_path)
        tmp_path = None

        logger.info(
            "expert_video task DONE | task_id=%s KB_draft=%s extracted_segments=%d",
            task_id, kb_version, len(extraction_results),
        )
        return {
            "task_id": task_id_str,
            "status": "success",
            "kb_version_draft": kb_version,
            "extracted_segments": len(extraction_results),
        }

    except Exception as exc:
        _cleanup(tmp_path)
        tmp_path = None
        logger.exception("Unhandled error in expert_video task %s", task_id)
        try:
            asyncio.run(_set_failed(
                task_id, f"INTERNAL_ERROR: {type(exc).__name__}: {exc}"
            ))
        except Exception:  # noqa: BLE001
            pass
        raise self.retry(exc=exc)
