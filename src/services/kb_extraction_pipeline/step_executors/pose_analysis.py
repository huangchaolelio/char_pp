"""pose_analysis executor (Feature 015 + Feature-016 US2 rewrite).

Pre-US2 behavior: consumed a single ``video.mp4`` and ran one pose-estimator
pass over the full clip. The clip-level decode + full CUDA graph allocation
could OOM the 64 GB pod on long videos.

US2 behavior: consumes the segmented output from the new ``download_video``
executor:
  - ``download_video`` now emits ``output_artifact_path`` = **directory** that
    contains ``segments/seg_NNNN.mp4`` + (optional) ``audio.wav``;
  - We load the corresponding ``video_preprocessing`` view via
    ``preprocessing_service`` to recover each segment's ``start_ms`` (needed
    to rebase frame timestamps onto the original-video timeline);
  - Iterate segments in order, call ``estimate_pose`` per segment (ships
    frames with segment-local timestamps), rebase to global timeline, and
    accumulate into one list;
  - Serialise the accumulated frames to ``<job_dir>/pose.json`` (unchanged
    contract with visual_kb_extract);
  - Report ``segments_processed`` / ``segments_failed`` + backend in
    ``output_summary`` (observability — FR-014).

Error behavior:
  - ``video_validator.validate_video`` still runs (on segment 0) as a cheap
    quality gate — mis-classified videos are rejected before any pose work.
  - Per-segment ``estimate_pose`` failures still abort the whole step — we
    have no sensible "partial pose.json" story for KB extraction.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.services import pose_estimator, preprocessing_service, video_validator
from src.services.kb_extraction_pipeline.artifact_io import write_pose_artifact
from src.services.kb_extraction_pipeline.error_codes import (
    POSE_NO_KEYPOINTS,
    VIDEO_QUALITY_REJECTED,
    format_error,
)


logger = logging.getLogger(__name__)


# ── Module-level helpers (monkeypatch targets) ──────────────────────────────

async def _get_video_path(
    session: AsyncSession,
    job: ExtractionJob,
    step_id: Any | None = None,
) -> str:
    """Resolve the local download_video artifact from the upstream step.

    In the US2 world this is a **directory**, not a file. Kept the same
    signature (return str) for backward compat with the earlier test suite.
    """
    artifact_path = (
        await session.execute(
            select(PipelineStep.output_artifact_path).where(
                PipelineStep.job_id == job.id,
                PipelineStep.step_type == StepType.download_video,
            )
        )
    ).scalar_one_or_none()
    if not artifact_path or not Path(artifact_path).exists():
        raise RuntimeError(
            "download_video artifact missing — cannot run pose analysis"
        )
    return str(artifact_path)


async def _load_preprocessing_view(session: AsyncSession, cos_object_key: str):
    """Load the success preprocessing view (for segment timing metadata)."""
    row = await preprocessing_service._fetch_success_job(session, cos_object_key)
    if row is None:
        raise RuntimeError(
            f"no success preprocessing job for {cos_object_key!r}"
        )
    view = await preprocessing_service.get_job_view(session, row.id)
    if view is None:  # pragma: no cover
        raise RuntimeError(f"preprocessing job {row.id} view unavailable")
    return view


# ── Main executor ───────────────────────────────────────────────────────────

async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Run pose estimation over each preprocessed segment, accumulating frames."""
    download_dir_str = await _get_video_path(
        session, job, getattr(step, "id", None),
    )
    download_dir = Path(download_dir_str)

    # download_video now emits a directory. Legacy path (single file) still
    # supported for tests not yet migrated.
    if download_dir.is_file():
        download_dir = download_dir.parent

    settings = get_settings()

    # Load preprocessing view for segment timing.
    view = await _load_preprocessing_view(session, job.cos_object_key)

    segments = sorted(view.segments, key=lambda s: s.segment_index)
    if not segments:  # pragma: no cover — defensive
        raise RuntimeError(
            f"preprocessing view has 0 segments for {job.cos_object_key!r}"
        )

    # Quality gate on segment 0 (cheap — ffprobe on first 3 minutes).
    seg0_path = download_dir / "segments" / f"seg_{segments[0].segment_index:04d}.mp4"
    try:
        video_meta = await asyncio.to_thread(
            video_validator.validate_video, seg0_path,
        )
    except video_validator.VideoQualityRejected as exc:
        raise RuntimeError(
            format_error(VIDEO_QUALITY_REJECTED, _format_validator_details(exc))
        ) from exc

    # ── Iterate segments, accumulate frames ──────────────────────────────
    all_frames: list = []
    segments_processed = 0
    segments_failed = 0

    for seg in segments:
        seg_path = download_dir / "segments" / f"seg_{seg.segment_index:04d}.mp4"
        if not seg_path.exists():
            segments_failed += 1
            raise RuntimeError(
                f"segment file missing in job dir: {seg_path} "
                f"(segment_index={seg.segment_index})"
            )

        seg_frames = await asyncio.to_thread(pose_estimator.estimate_pose, seg_path)

        # Rebase timestamps onto original-video timeline.
        offset_ms = int(seg.start_ms)
        for frame in seg_frames:
            if hasattr(frame, "timestamp_ms") and frame.timestamp_ms is not None:
                frame.timestamp_ms = int(frame.timestamp_ms) + offset_ms

        all_frames.extend(seg_frames)
        segments_processed += 1

    if not all_frames:
        raise RuntimeError(
            format_error(
                POSE_NO_KEYPOINTS,
                f"estimate_pose returned 0 frames across {segments_processed} segments",
            )
        )

    # ── Serialise accumulated pose to pose.json ──────────────────────────
    out_path = download_dir / "pose.json"

    # Assemble meta from the preprocessing view (authoritative for the full
    # original-video duration), falling back to segment 0 probe if missing.
    if view.original_meta:
        meta_dict = {
            "fps": float(view.original_meta.get("fps") or video_meta.fps),
            "width": int(view.original_meta.get("width") or video_meta.width),
            "height": int(view.original_meta.get("height") or video_meta.height),
            "duration_seconds": float(
                (view.original_meta.get("duration_ms") or 0) / 1000
                or video_meta.duration_seconds
            ),
            "frame_count": int(video_meta.frame_count),
        }
    else:
        meta_dict = {
            "fps": float(video_meta.fps),
            "width": int(video_meta.width),
            "height": int(video_meta.height),
            "duration_seconds": float(video_meta.duration_seconds),
            "frame_count": int(video_meta.frame_count),
        }

    backend = _resolve_effective_backend(settings.pose_backend)

    await asyncio.to_thread(
        write_pose_artifact,
        out_path,
        video_path=str(download_dir),
        video_meta=meta_dict,
        backend=backend,
        frames=all_frames,
    )

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "keypoints_frame_count": len(all_frames),
            "detected_segments": 0,  # visual_kb_extract fills the real count
            "backend": backend,
            "video_duration_sec": meta_dict["duration_seconds"],
            "fps": meta_dict["fps"],
            "resolution": f"{meta_dict['width']}x{meta_dict['height']}",
            "segments_processed": segments_processed,
            "segments_failed": segments_failed,
        },
        "output_artifact_path": str(out_path),
    }


def _format_validator_details(exc: video_validator.VideoQualityRejected) -> str:
    """Render VideoQualityRejected metadata into a grep-friendly string."""
    parts: list[str] = [f"reason={exc.reason}"]
    for key in ("field", "actual", "threshold", "fps", "resolution",
                "min_required_fps", "min_required_resolution"):
        if key in exc.details:
            parts.append(f"{key}={exc.details[key]}")
    return " ".join(parts)


def _resolve_effective_backend(requested: str) -> str:
    """Return the backend actually used by ``pose_estimator``."""
    try:
        return pose_estimator._detect_backend(requested)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover — defensive
        return requested or "unknown"
