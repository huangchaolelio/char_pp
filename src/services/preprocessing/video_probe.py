"""Feature-016 — probe + validate a local video before standardisation.

``probe_and_validate(local_path)`` is the unified entry point called from
``orchestrator.run_preprocessing`` at the start of every job:

1. ffprobe → extract fps / width / height / duration_ms / codec / size_bytes /
   has_audio (JSON output).
2. validate_video (Feature-002) → enforces fps ≥ 15 and resolution ≥ 854x480.
3. Map every failure to a ``RuntimeError`` with a structured prefix
   (see ``error_codes``) so the orchestrator can persist it to
   ``VideoPreprocessingJob.error_message`` without additional handling.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.services import video_validator
from src.services.preprocessing import error_codes
from src.services.video_validator import validate_video


logger = logging.getLogger(__name__)


@dataclass
class ProbedVideoMeta:
    """Metadata captured by ``ffprobe`` + ``validate_video``.

    All byte / ms values are plain integers so they serialise cleanly to JSONB
    (see ``VideoPreprocessingJob.original_meta_json``).
    """

    fps: float
    width: int
    height: int
    duration_ms: int
    codec: str
    size_bytes: int
    has_audio: bool

    def to_json_dict(self) -> dict[str, object]:
        return {
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "duration_ms": self.duration_ms,
            "codec": self.codec,
            "size_bytes": self.size_bytes,
            "has_audio": self.has_audio,
        }


def _parse_fps(r_frame_rate: Optional[str]) -> float:
    """Parse ffprobe-style rational ``num/den`` into fps float."""
    if not r_frame_rate or "/" not in r_frame_rate:
        return 0.0
    num_s, den_s = r_frame_rate.split("/", 1)
    try:
        num, den = int(num_s), int(den_s)
        if den == 0:
            return 0.0
        return num / den
    except ValueError:
        return 0.0


def probe_and_validate(local_path: Path) -> ProbedVideoMeta:
    """Run ffprobe on ``local_path``, validate it, return its metadata.

    Raises:
        RuntimeError with one of these prefixes:
            ``VIDEO_PROBE_FAILED:``   — ffprobe returned non-zero or invalid JSON
            ``VIDEO_CODEC_UNSUPPORTED:`` — no decodable video stream found
            ``VIDEO_QUALITY_REJECTED:`` — fps / resolution below project minimum
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(local_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_PROBE_FAILED, f"ffprobe invocation failed: {exc}"
            )
        ) from exc

    if proc.returncode != 0:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_PROBE_FAILED,
                f"ffprobe exit={proc.returncode} stderr={proc.stderr.strip()[:200]}",
            )
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_PROBE_FAILED,
                f"ffprobe output not JSON: {exc}",
            )
        ) from exc

    streams = data.get("streams", []) or []
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_CODEC_UNSUPPORTED,
                "no decodable video stream found",
            )
        )
    audio_present = any(s.get("codec_type") == "audio" for s in streams)

    fps = _parse_fps(video_stream.get("r_frame_rate"))
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    codec = str(video_stream.get("codec_name") or "unknown")
    duration_s = float(
        video_stream.get("duration")
        or data.get("format", {}).get("duration")
        or 0.0
    )
    size_bytes = int(data.get("format", {}).get("size") or 0)
    # Fall back to on-disk size if ffprobe did not report one.
    if size_bytes == 0:
        try:
            size_bytes = local_path.stat().st_size
        except OSError:
            size_bytes = 0

    meta = ProbedVideoMeta(
        fps=fps,
        width=width,
        height=height,
        duration_ms=int(math.floor(duration_s * 1000)) if duration_s > 0 else 0,
        codec=codec,
        size_bytes=size_bytes,
        has_audio=audio_present,
    )

    # ── FR-002a quality gate (reuse Feature-002 validator) ──────────────────
    try:
        validate_video(local_path)
    except video_validator.VideoQualityRejected as exc:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_QUALITY_REJECTED, str(exc)
            )
        ) from exc

    logger.info(
        "probe ok — path=%s fps=%.2f %dx%d codec=%s duration_ms=%d has_audio=%s",
        local_path, meta.fps, meta.width, meta.height, meta.codec,
        meta.duration_ms, meta.has_audio,
    )
    return meta
