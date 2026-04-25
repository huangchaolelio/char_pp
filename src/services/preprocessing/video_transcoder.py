"""Feature-016 — standardise a video to the project's target fps/short-side.

``transcode(input_path, output_path, target_fps, target_short_side)`` wraps
ffmpeg with a scale filter that preserves aspect ratio:

- landscape (W > H) → scale to height=target_short_side
- portrait or square → scale to width=target_short_side

Audio is dropped (``-an``) because ``audio_exporter`` produces a separate
16 kHz mono WAV from the ORIGINAL video, which is both higher quality
and reusable by KB extraction without re-decoding.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from src.services.preprocessing import error_codes


logger = logging.getLogger(__name__)


def _build_scale_filter(target_short_side: int) -> str:
    """Return an ffmpeg -vf expression preserving aspect ratio.

    ``-2`` in either dimension tells ffmpeg "compute a value divisible by 2".
    """
    s = target_short_side
    return f"scale='if(gt(iw,ih),-2,{s})':'if(gt(iw,ih),{s},-2)'"


def transcode(
    input_path: Path,
    output_path: Path,
    *,
    target_fps: int,
    target_short_side: int,
    timeout_seconds: int = 60 * 60,
) -> Path:
    """Transcode ``input_path`` into a standardised ``output_path``.

    Args:
        input_path: local source video (already probed).
        output_path: local destination — parent directory must already exist.
        target_fps: frame rate of the standardised output.
        target_short_side: short-edge pixel target (aspect-ratio preserving).
        timeout_seconds: ffmpeg hard timeout.

    Returns:
        ``output_path`` on success (for fluent chaining).

    Raises:
        RuntimeError prefixed ``VIDEO_TRANSCODE_FAILED:``.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-i", str(input_path),
        "-vf", _build_scale_filter(target_short_side),
        "-r", str(target_fps),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",  # strip audio — handled by audio_exporter from the ORIGINAL
        str(output_path),
    ]
    logger.info("transcode → %s (target_fps=%d short=%d)",
                output_path, target_fps, target_short_side)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_TRANSCODE_FAILED,
                f"ffmpeg invocation failed: {exc}",
            )
        ) from exc

    if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_TRANSCODE_FAILED,
                f"ffmpeg exit={proc.returncode} "
                f"stderr={proc.stderr.strip()[:300]}",
            )
        )
    return output_path
