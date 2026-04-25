"""Feature-016 — streaming split a standardised video into N × T-second segments.

``split(...)`` is a generator so the uploader can consume segments in parallel
as ffmpeg produces them (FR-005c). Since ffmpeg's ``-f segment`` muxer only
guarantees keyframe-aligned boundaries, we probe each produced file for its
actual duration to compute accurate ``start_ms``/``end_ms`` (SC-005).
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from src.services.preprocessing import error_codes


logger = logging.getLogger(__name__)


@dataclass
class SegmentInfo:
    segment_index: int
    start_ms: int
    end_ms: int
    local_path: Path


def _run_ffmpeg_split(
    input_path: Path,
    output_dir: Path,
    segment_duration_s: int,
    timeout_seconds: int = 60 * 60,
) -> None:
    """Invoke ffmpeg to emit ``seg_NNNN.mp4`` into ``output_dir``.

    Uses stream copy so split is I/O-bound (no re-encode).
    """
    pattern = str(output_dir / "seg_%04d.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-i", str(input_path),
        "-c", "copy",
        "-map", "0",
        "-f", "segment",
        "-segment_time", str(segment_duration_s),
        "-reset_timestamps", "1",
        pattern,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"ffmpeg split failed: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg exit={proc.returncode} stderr={proc.stderr.strip()[:300]}"
        )


def _probe_segment_duration_ms(local_path: Path) -> int:
    """Return integer milliseconds using ``ffprobe``."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nokey=1:noprint_wrappers=1",
        str(local_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"ffprobe failed on {local_path}: {exc}") from exc
    try:
        return int(math.floor(float(proc.stdout.strip()) * 1000))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"ffprobe invalid duration for {local_path}: {proc.stdout!r}"
        ) from exc


def split(
    *,
    input_path: Path,
    output_dir: Path,
    total_duration_ms: int,
    segment_duration_s: int,
) -> Iterator[SegmentInfo]:
    """Split the standardised video, yielding ``SegmentInfo`` per segment.

    Raises:
        RuntimeError prefixed ``VIDEO_SPLIT_FAILED:``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _run_ffmpeg_split(input_path, output_dir, segment_duration_s)
    except Exception as exc:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_SPLIT_FAILED, str(exc)
            )
        ) from exc

    expected = max(1, math.ceil(total_duration_ms / (segment_duration_s * 1000)))
    files = sorted(output_dir.glob("seg_[0-9]*.mp4"))
    if not files:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_SPLIT_FAILED,
                f"no segments produced in {output_dir}",
            )
        )

    cursor_ms = 0
    for idx, fp in enumerate(files):
        try:
            duration_ms = _probe_segment_duration_ms(fp)
        except Exception as exc:
            raise RuntimeError(
                error_codes.format_error(
                    error_codes.VIDEO_SPLIT_FAILED, str(exc)
                )
            ) from exc
        # Clamp the last segment's end_ms to total_duration_ms so cumulative
        # sum matches source (SC-005 floor) even if ffprobe rounds.
        start_ms = cursor_ms
        end_ms = cursor_ms + duration_ms
        is_last = idx == len(files) - 1
        if is_last:
            end_ms = max(end_ms, total_duration_ms)
        yield SegmentInfo(
            segment_index=idx,
            start_ms=start_ms,
            end_ms=end_ms,
            local_path=fp,
        )
        cursor_ms = end_ms

    if len(files) != expected:
        logger.warning(
            "splitter produced %d segments, expected %d (tolerated by SC-005)",
            len(files), expected,
        )
