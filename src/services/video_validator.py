"""Video quality gate — rejects inputs that cannot produce reliable analysis.

Quality rules (from spec clarifications and plan.md):
  - fps >= 15
  - resolution >= 854 x 480
  - File must be readable by OpenCV

Returns VideoMeta on success; raises VideoQualityRejected with a machine-readable
reason on failure so callers can map it to the API error code VIDEO_QUALITY_REJECTED.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

try:
    import cv2  # type: ignore[import]
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class VideoQualityRejected(Exception):
    """Raised when a video does not meet the minimum quality requirements."""

    def __init__(self, reason: str, details: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details: dict = details or {}


@dataclass
class VideoMeta:
    fps: float
    width: int
    height: int
    duration_seconds: float
    frame_count: int

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"


def validate_video(video_path: Path) -> VideoMeta:
    """Validate video quality and return metadata.

    Args:
        video_path: Path to a local video file.

    Returns:
        VideoMeta with fps, resolution, and duration.

    Raises:
        VideoQualityRejected: if any quality gate fails.
    """
    if cv2 is None:  # pragma: no cover
        raise RuntimeError("opencv-python-headless is not installed")

    from src.config import get_settings
    settings = get_settings()

    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise VideoQualityRejected(
                "unreadable",
                details={"path": str(video_path), "error": "OpenCV cannot open file"},
            )

        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_seconds = frame_count / fps if fps > 0 else 0.0

        logger.debug(
            "Video probe: fps=%.1f width=%d height=%d frames=%d duration=%.1fs",
            fps, width, height, frame_count, duration_seconds,
        )

        if fps < settings.min_video_fps:
            raise VideoQualityRejected(
                "fps_too_low",
                details={
                    "fps": fps,
                    "min_required_fps": settings.min_video_fps,
                },
            )

        if width < settings.min_video_width or height < settings.min_video_height:
            raise VideoQualityRejected(
                "resolution_too_low",
                details={
                    "resolution": f"{width}x{height}",
                    "min_required_resolution": (
                        f"{settings.min_video_width}x{settings.min_video_height}"
                    ),
                },
            )

        return VideoMeta(
            fps=fps,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            frame_count=frame_count,
        )
    finally:
        cap.release()
