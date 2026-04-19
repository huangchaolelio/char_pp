"""Celery task: process an athlete's video and compute deviation reports + coaching advice.

Flow (T035):
  1. Mark task as processing
  2. Validate video quality → reject if below threshold
  3. Check for active KB version → fail with KNOWLEDGE_BASE_NOT_READY if none
  4. Run MediaPipe pose estimation on every frame
  5. Segment frames into discrete action clips (wrist-velocity peaks)
  6. Classify each segment
  7. For each classified segment (skip unknown for deviation analysis):
     a. Compute measured parameters from pose sequence
     b. Persist AthleteMotionAnalysis
     c. Compute and persist DeviationReport records
     d. Compute stability for each deviation dimension
     e. Generate and persist CoachingAdvice records
  8. Clean up temp file
  9. Update task status = success

Error handling:
  - VideoQualityRejected         → status=rejected, rejection_reason set
  - KNOWLEDGE_BASE_NOT_READY     → status=failed, error_message set
  - Any other exception          → status=failed, task retried (max 2x)
"""

from __future__ import annotations


import asyncio
import logging
import math
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from celery import shared_task
from sqlalchemy import select

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus
from src.models.athlete_motion_analysis import AthleteActionType, AthleteMotionAnalysis
from src.models.deviation_report import DeviationReport
from src.models.expert_tech_point import ExpertTechPoint
from src.services import (
    action_classifier,
    action_segmenter,
    advice_generator,
    deviation_analyzer,
    knowledge_base_svc,
    pose_estimator,
    video_validator,
)
from src.services.pose_estimator import (
    LANDMARK_LEFT_ELBOW,
    LANDMARK_LEFT_HIP,
    LANDMARK_LEFT_SHOULDER,
    LANDMARK_LEFT_WRIST,
    LANDMARK_RIGHT_ELBOW,
    LANDMARK_RIGHT_HIP,
    LANDMARK_RIGHT_SHOULDER,
    LANDMARK_RIGHT_WRIST,
    FramePoseResult,
)
from src.services.video_validator import VideoMeta, VideoQualityRejected

logger = logging.getLogger(__name__)

_LOW_CONFIDENCE_THRESHOLD = 0.7


def _make_session_factory():
    """Create a fresh async engine + sessionmaker for each Celery task invocation.

    Avoids 'Future attached to a different loop' errors in forked Celery workers.
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


# ── Measured parameter extraction ─────────────────────────────────────────────

def _safe_mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _extract_measured_params(
    frames: list[FramePoseResult],
    segment_start_ms: int,
) -> tuple[dict, float]:
    """Compute measured parameters for a single action segment.

    Returns:
        (measured_params, overall_confidence) where measured_params has structure:
        {"dimension": {"value": float, "unit": str, "confidence": float}}
    """
    params: dict = {}
    all_confidences: list[float] = []

    # 1. elbow_angle
    angles, angle_confs = [], []
    for frame in frames:
        for s_idx, e_idx, w_idx in [
            (LANDMARK_RIGHT_SHOULDER, LANDMARK_RIGHT_ELBOW, LANDMARK_RIGHT_WRIST),
            (LANDMARK_LEFT_SHOULDER, LANDMARK_LEFT_ELBOW, LANDMARK_LEFT_WRIST),
        ]:
            s = frame.keypoints.get(s_idx)
            e = frame.keypoints.get(e_idx)
            w = frame.keypoints.get(w_idx)
            if s and e and w:
                ax, ay = s.x - e.x, s.y - e.y
                cx, cy = w.x - e.x, w.y - e.y
                dot = ax * cx + ay * cy
                mag = math.hypot(ax, ay) * math.hypot(cx, cy)
                if mag > 1e-9:
                    angle = math.degrees(math.acos(max(-1.0, min(1.0, dot / mag))))
                    conf = min(s.visibility, e.visibility, w.visibility)
                    angles.append(angle)
                    angle_confs.append(conf)
                break
    if angles:
        conf = _safe_mean(angle_confs) or 0.0
        all_confidences.append(conf)
        params["elbow_angle"] = {
            "value": _safe_mean(angles),
            "unit": "°",
            "confidence": conf,
        }

    # 2. swing_trajectory
    arc_lengths, shoulder_widths, traj_confs = [], [], []
    for i in range(1, len(frames)):
        for w_idx in (LANDMARK_RIGHT_WRIST, LANDMARK_LEFT_WRIST):
            kp_c = frames[i].keypoints.get(w_idx)
            kp_p = frames[i - 1].keypoints.get(w_idx)
            if kp_c and kp_p:
                arc_lengths.append(math.hypot(kp_c.x - kp_p.x, kp_c.y - kp_p.y))
                traj_confs.append(min(kp_c.visibility, kp_p.visibility))
                break
    for frame in frames:
        ls = frame.keypoints.get(LANDMARK_LEFT_SHOULDER)
        rs = frame.keypoints.get(LANDMARK_RIGHT_SHOULDER)
        if ls and rs:
            shoulder_widths.append(math.hypot(ls.x - rs.x, ls.y - rs.y))
    if arc_lengths and shoulder_widths:
        mean_sw = _safe_mean(shoulder_widths) or 0.1
        ratio = sum(arc_lengths) / mean_sw if mean_sw > 1e-9 else None
        conf = _safe_mean(traj_confs) or 0.0
        all_confidences.append(conf)
        if ratio is not None:
            params["swing_trajectory"] = {
                "value": ratio,
                "unit": "ratio",
                "confidence": conf,
            }

    # 3. contact_timing
    if len(frames) >= 2:
        best_v, best_ts = 0.0, frames[0].timestamp_ms
        ct_confs = []
        for i in range(1, len(frames)):
            for w_idx in (LANDMARK_RIGHT_WRIST, LANDMARK_LEFT_WRIST):
                kp_c = frames[i].keypoints.get(w_idx)
                kp_p = frames[i - 1].keypoints.get(w_idx)
                if kp_c and kp_p:
                    dt = (frames[i].timestamp_ms - frames[i - 1].timestamp_ms) / 1000.0
                    if dt > 0:
                        v = math.hypot(kp_c.x - kp_p.x, kp_c.y - kp_p.y) / dt
                        ct_confs.append(min(kp_c.visibility, kp_p.visibility))
                        if v > best_v:
                            best_v, best_ts = v, frames[i].timestamp_ms
                    break
        if ct_confs:
            timing = float(best_ts - segment_start_ms)
            conf = _safe_mean(ct_confs) or 0.0
            all_confidences.append(conf)
            params["contact_timing"] = {
                "value": timing,
                "unit": "ms",
                "confidence": conf,
            }

    # 4. weight_transfer
    hip_shifts, wt_confs = [], []
    for i in range(1, len(frames)):
        lh_c = frames[i].keypoints.get(LANDMARK_LEFT_HIP)
        rh_c = frames[i].keypoints.get(LANDMARK_RIGHT_HIP)
        lh_p = frames[i - 1].keypoints.get(LANDMARK_LEFT_HIP)
        rh_p = frames[i - 1].keypoints.get(LANDMARK_RIGHT_HIP)
        if lh_c and rh_c and lh_p and rh_p:
            mid_c = (lh_c.x + rh_c.x) / 2
            mid_p = (lh_p.x + rh_p.x) / 2
            hip_shifts.append(abs(mid_c - mid_p))
            wt_confs.append(min(lh_c.visibility, rh_c.visibility, lh_p.visibility, rh_p.visibility))
    sw_list = []
    for frame in frames:
        ls = frame.keypoints.get(LANDMARK_LEFT_SHOULDER)
        rs = frame.keypoints.get(LANDMARK_RIGHT_SHOULDER)
        if ls and rs:
            sw_list.append(math.hypot(ls.x - rs.x, ls.y - rs.y))
    if hip_shifts and sw_list:
        mean_sw = _safe_mean(sw_list) or 0.1
        wt_ratio = sum(hip_shifts) / mean_sw if mean_sw > 1e-9 else None
        conf = _safe_mean(wt_confs) or 0.0
        all_confidences.append(conf)
        if wt_ratio is not None:
            params["weight_transfer"] = {
                "value": wt_ratio,
                "unit": "ratio",
                "confidence": conf,
            }

    overall_confidence = _safe_mean(all_confidences) or 0.0
    return params, overall_confidence


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


async def _persist_athlete_results(
    task_id: uuid.UUID,
    video_meta: VideoMeta,
    kb_version: str,
    classified_segments: list,
    all_frames: list[FramePoseResult],
) -> None:
    """Persist AthleteMotionAnalysis, DeviationReports, and CoachingAdvice in one transaction."""
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            # Fetch expert points for this KB version
            ep_result = await session.execute(
                select(ExpertTechPoint).where(
                    ExpertTechPoint.knowledge_base_version == kb_version
                )
            )
            all_expert_points = ep_result.scalars().all()

            # Group expert points by action_type
            ep_by_action: dict[str, list[ExpertTechPoint]] = {}
            ep_by_id: dict[uuid.UUID, ExpertTechPoint] = {}
            for ep in all_expert_points:
                at = ep.action_type.value
                ep_by_action.setdefault(at, []).append(ep)
                ep_by_id[ep.id] = ep

            # Update task metadata
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.video_fps = video_meta.fps
                task.video_resolution = video_meta.resolution_str
                task.video_duration_seconds = video_meta.duration_seconds
                task.knowledge_base_version = kb_version

            # Collect all motion analyses for stability computation
            motion_analyses = []

            for cs in classified_segments:
                seg_frames = action_segmenter.frames_for_segment(all_frames, cs.segment)
                measured_params, overall_conf = _extract_measured_params(
                    seg_frames, cs.segment.start_ms
                )

                try:
                    athlete_action_type = AthleteActionType(cs.action_type)
                except ValueError:
                    athlete_action_type = AthleteActionType.unknown

                analysis = AthleteMotionAnalysis(
                    task_id=task_id,
                    action_type=athlete_action_type,
                    segment_start_ms=cs.segment.start_ms,
                    segment_end_ms=cs.segment.end_ms,
                    measured_params=measured_params,
                    overall_confidence=overall_conf,
                    is_low_confidence=overall_conf < _LOW_CONFIDENCE_THRESHOLD,
                    knowledge_base_version=kb_version,
                )
                session.add(analysis)
                await session.flush()
                motion_analyses.append((analysis, cs.action_type))

                # Skip deviation analysis for unknown action types
                if cs.action_type == "unknown":
                    continue

                expert_points = ep_by_action.get(cs.action_type, [])
                if not expert_points:
                    logger.warning(
                        "No expert points for action_type=%s in KB %s",
                        cs.action_type, kb_version,
                    )
                    continue

                # Compute deviations
                reports = await deviation_analyzer.analyze_deviations(
                    session, analysis, expert_points
                )

                # Generate advice
                await advice_generator.generate_advice(
                    session=session,
                    task_id=task_id,
                    deviation_reports=reports,
                    expert_points_by_id=ep_by_id,
                    action_type=cs.action_type,
                )

            # Stability computation: gather all analysis IDs for this task
            all_analysis_ids = [a.id for a, _ in motion_analyses]
            if len(all_analysis_ids) >= 3:
                # Re-query deviation reports to update is_stable_deviation
                for analysis, action_type in motion_analyses:
                    if action_type == "unknown":
                        continue
                    dr_result = await session.execute(
                        select(DeviationReport).where(
                            DeviationReport.analysis_id == analysis.id
                        )
                    )
                    reports_for_analysis = dr_result.scalars().all()
                    for dr in reports_for_analysis:
                        is_stable = await deviation_analyzer.compute_stability(
                            session, all_analysis_ids, action_type, dr.dimension
                        )
                        dr.is_stable_deviation = is_stable

            # Mark task as success
            if task:
                task.status = TaskStatus.success
                task.completed_at = datetime.now(tz=timezone.utc)

            logger.info(
                "Persisted athlete results: %d segments, KB=%s, task=%s",
                len(classified_segments), kb_version, task_id,
            )


def _cleanup(tmp_path: Path | None) -> None:
    """Best-effort temp file cleanup."""
    if tmp_path is None:
        return
    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except Exception:  # noqa: BLE001
        logger.debug("Temp file cleanup skipped: %s", tmp_path)


# ── Celery task ────────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name="src.workers.athlete_video_task.process_athlete_video",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def process_athlete_video(
    self,
    task_id_str: str,
    tmp_path_str: str,
    kb_version: str | None = None,
    target_person_index: int | None = None,
) -> dict:
    """Process an athlete's video and produce deviation reports + coaching advice.

    Args:
        task_id_str: String UUID of the AnalysisTask.
        tmp_path_str: Path to the uploaded video file in the temp directory.
        kb_version: Specific KB version to use; if None, uses the active version.
        target_person_index: Ignored in v1 (single-person detection).

    Returns:
        Dict with task_id, status, and summary on success.
    """
    task_id = uuid.UUID(task_id_str)
    tmp_path = Path(tmp_path_str)

    logger.info(
        "athlete_video task started | task_id=%s tmp=%s", task_id, tmp_path
    )

    try:
        # ── 1. Mark as processing ──────────────────────────────────────────────
        asyncio.run(_set_processing(task_id))

        # ── 2. Validate video quality ──────────────────────────────────────────
        try:
            video_meta = video_validator.validate_video(tmp_path)
            logger.info(
                "Video validated: fps=%.1f res=%s dur=%.1fs (task %s)",
                video_meta.fps, video_meta.resolution_str, video_meta.duration_seconds, task_id,
            )
        except VideoQualityRejected as exc:
            _cleanup(tmp_path)
            asyncio.run(_set_rejected(task_id, f"{exc.reason}: {exc.details}"))
            return {
                "task_id": task_id_str,
                "status": "rejected",
                "reason": exc.reason,
            }

        # ── 3. Resolve KB version ──────────────────────────────────────────────
        async def _get_kb() -> str | None:
            factory = _make_session_factory()
            async with factory() as session:
                if kb_version:
                    kb = await knowledge_base_svc.get_version(session, kb_version)
                    return kb.version if kb else None
                active = await knowledge_base_svc.get_active_version(session)
                return active.version if active else None

        resolved_kb = asyncio.run(_get_kb())
        if resolved_kb is None:
            _cleanup(tmp_path)
            asyncio.run(_set_failed(task_id, "KNOWLEDGE_BASE_NOT_READY"))
            return {
                "task_id": task_id_str,
                "status": "failed",
                "error": "KNOWLEDGE_BASE_NOT_READY",
            }

        # ── 4. Pose estimation (with ffmpeg pre-clip OOM guard) ───────────────
        # Clip to 60s + downscale to 720p to avoid OOM on large videos
        clipped_path = tmp_path.parent / f"clip_{tmp_path.stem}.mp4"
        ffmpeg_bin = shutil.which("ffmpeg") or "/opt/conda/bin/ffmpeg"
        clip_cmd = [
            ffmpeg_bin, "-y", "-t", "60",
            "-i", str(tmp_path),
            "-vf", "scale=1280:720",
            "-c:v", "libx264", "-crf", "23", "-an",
            str(clipped_path),
        ]
        clip_result = subprocess.run(clip_cmd, capture_output=True, timeout=120)
        if clip_result.returncode == 0 and clipped_path.exists():
            _cleanup(tmp_path)
            tmp_path = clipped_path

        logger.info("Running pose estimation (task %s)…", task_id)
        all_frames = pose_estimator.estimate_pose(tmp_path)

        if not all_frames:
            _cleanup(tmp_path)
            asyncio.run(_set_failed(task_id, "NO_MOTION_DETECTED"))
            return {"task_id": task_id_str, "status": "failed", "error": "NO_MOTION_DETECTED"}

        # ── 5. Segment actions ─────────────────────────────────────────────────
        segments = action_segmenter.segment_actions(all_frames)
        logger.info("%d action segments detected (task %s)", len(segments), task_id)

        # ── 6. Classify segments ───────────────────────────────────────────────
        classified_segments = []
        for seg in segments:
            seg_frames = action_segmenter.frames_for_segment(all_frames, seg)
            classified = action_classifier.classify_segment(seg_frames, seg)
            classified_segments.append(classified)

        # ── 7. Persist results ─────────────────────────────────────────────────
        asyncio.run(
            _persist_athlete_results(
                task_id=task_id,
                video_meta=video_meta,
                kb_version=resolved_kb,
                classified_segments=classified_segments,
                all_frames=all_frames,
            )
        )

        # ── 8. Cleanup ─────────────────────────────────────────────────────────
        _cleanup(tmp_path)

        analyzed = sum(1 for cs in classified_segments if cs.action_type != "unknown")
        logger.info(
            "athlete_video task DONE | task_id=%s segments=%d analyzed=%d KB=%s",
            task_id, len(classified_segments), analyzed, resolved_kb,
        )
        return {
            "task_id": task_id_str,
            "status": "success",
            "total_segments": len(classified_segments),
            "analyzed_segments": analyzed,
            "kb_version": resolved_kb,
        }

    except Exception as exc:
        _cleanup(tmp_path)
        logger.exception("Unhandled error in athlete_video task %s", task_id)
        try:
            asyncio.run(_set_failed(
                task_id, f"INTERNAL_ERROR: {type(exc).__name__}: {exc}"
            ))
        except Exception:  # noqa: BLE001
            pass
        raise self.retry(exc=exc)


# ── Data retention cleanup task (T042) ────────────────────────────────────────

@shared_task(
    name="src.workers.athlete_video_task.cleanup_expired_tasks",
    bind=False,
)
def cleanup_expired_tasks() -> dict:
    """Daily cleanup: physically delete expired/soft-deleted analysis tasks.

    Removes tasks where:
      - deleted_at IS NOT NULL (user explicitly deleted), OR
      - completed_at < NOW() - 12 months (data retention expiry)

    Cascade deletes all associated data:
      - AthleteMotionAnalysis, DeviationReport, CoachingAdvice, ExpertTechPoint

    Logs the number of records cleaned.
    """
    async def _run_cleanup() -> int:
        from sqlalchemy import delete, or_

        settings = get_settings()
        retention_months = settings.data_retention_months
        cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=retention_months * 30)
        _factory = _make_session_factory()

        async with _factory() as session:
            async with session.begin():
                from src.models.analysis_task import AnalysisTask as AT
                stmt = delete(AT).where(
                    or_(
                        AT.deleted_at.isnot(None),
                        AT.completed_at < cutoff_date,
                    )
                ).returning(AT.id)
                result = await session.execute(stmt)
                deleted_ids = result.fetchall()
                count = len(deleted_ids)

        return count

    count = asyncio.run(_run_cleanup())
    logger.info("Data retention cleanup: physically deleted %d expired tasks", count)
    return {"deleted_count": count}
