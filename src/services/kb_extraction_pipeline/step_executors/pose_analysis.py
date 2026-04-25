"""pose_analysis executor (Feature 015) — real YOLOv8 / MediaPipe integration.

Pipeline:
    1. Resolve the local video path from the upstream ``download_video`` step.
    2. Gate the video through Feature-002's quality validator. A ``VideoQualityRejected``
       exception is translated into a ``VIDEO_QUALITY_REJECTED:`` prefixed
       ``RuntimeError`` so operations can grep for it (FR-006 / FR-016).
    3. Run ``pose_estimator.estimate_pose`` inside ``asyncio.to_thread`` to
       keep the event loop free for the parallel ``audio_transcription`` sibling.
    4. Serialise the frame list to ``<job_dir>/pose.json`` via
       ``artifact_io.write_pose_artifact`` for ``visual_kb_extract`` to consume.
    5. Return a rich ``output_summary`` exposing the real backend + video meta
       (FR-014).
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
from src.services import pose_estimator, video_validator
from src.services.kb_extraction_pipeline.artifact_io import write_pose_artifact
from src.services.kb_extraction_pipeline.error_codes import (
    POSE_NO_KEYPOINTS,
    VIDEO_QUALITY_REJECTED,
    format_error,
)


logger = logging.getLogger(__name__)


async def _get_video_path(
    session: AsyncSession,
    job: ExtractionJob,
    step_id: Any | None = None,
) -> str:
    """Resolve the local video artifact path from the upstream download step.

    Split out as a module-level helper so unit tests can monkeypatch it
    without mocking the whole SQLAlchemy async session.
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


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Run real pose estimation over the downloaded video."""
    video_path = await _get_video_path(session, job, getattr(step, "id", None))
    video_path_obj = Path(video_path)

    settings = get_settings()

    # ── Step 1: validate_video (fast, CPU-bound) ──────────────────────────────
    try:
        video_meta = await asyncio.to_thread(video_validator.validate_video, video_path_obj)
    except video_validator.VideoQualityRejected as exc:
        details = _format_validator_details(exc)
        raise RuntimeError(format_error(VIDEO_QUALITY_REJECTED, details)) from exc

    # ── Step 2: estimate_pose (CPU/GPU-bound) ────────────────────────────────
    frames = await asyncio.to_thread(pose_estimator.estimate_pose, video_path_obj)

    if not frames:
        raise RuntimeError(
            format_error(POSE_NO_KEYPOINTS, "estimate_pose returned 0 frames")
        )

    # ── Step 3: serialise to pose.json for visual_kb_extract ─────────────────
    out_path = video_path_obj.parent / "pose.json"
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
        video_path=str(video_path_obj),
        video_meta=meta_dict,
        backend=backend,
        frames=frames,
    )

    # ── Step 4: output summary (FR-014) ──────────────────────────────────────
    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "keypoints_frame_count": len(frames),
            "detected_segments": 0,  # visual_kb_extract fills the real count
            "backend": backend,
            "video_duration_sec": meta_dict["duration_seconds"],
            "fps": meta_dict["fps"],
            "resolution": f"{meta_dict['width']}x{meta_dict['height']}",
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
    """Return the backend actually used by ``pose_estimator``.

    ``pose_estimator._detect_backend`` is a private helper; we call it to keep
    ``output_summary.backend`` accurate even when ``pose_backend='auto'``.
    Falls back to the raw setting if the helper signature changes.
    """
    try:
        return pose_estimator._detect_backend(requested)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover — defensive
        return requested or "unknown"
