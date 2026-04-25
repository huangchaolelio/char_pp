"""Feature-016 — extract the ORIGINAL video's audio track to a single WAV.

Produces a 16 kHz mono PCM S16LE WAV that KB-extraction's Whisper step
consumes as-is (FR-005a / FR-005b). We pull audio from the *original* video
rather than the post-transcode standardised file so we never double-decode.

Behaviour:
- Returns (Path, size_bytes) on success.
- Returns (None, 0) when the source has no audio stream — FR-008 states
  "no audio" must not fail preprocessing; the caller records ``has_audio=false``.
- All other failures raise RuntimeError prefixed ``AUDIO_EXTRACT_FAILED:``.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from src.services.preprocessing import error_codes


logger = logging.getLogger(__name__)


def export_wav(
    *,
    input_path: Path,
    output_path: Path,
    has_audio: bool,
    timeout_seconds: int = 60 * 30,
) -> tuple[Optional[Path], int]:
    """Extract a 16 kHz mono WAV into ``output_path``.

    Args:
        input_path: the ORIGINAL (pre-transcode) video.
        output_path: local destination; parent dir must exist.
        has_audio: probe-time hint. ``False`` → short-circuit with (None, 0).

    Returns:
        (local_path, size_bytes) on success, (None, 0) on "no audio".
    """
    if not has_audio:
        logger.info("audio_exporter: source has no audio stream — skip %s", input_path)
        return None, 0

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-i", str(input_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    logger.info("audio_exporter → %s", output_path)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.AUDIO_EXTRACT_FAILED,
                f"ffmpeg invocation failed: {exc}",
            )
        ) from exc

    if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.AUDIO_EXTRACT_FAILED,
                f"ffmpeg exit={proc.returncode} "
                f"stderr={proc.stderr.strip()[:300]}",
            )
        )
    return output_path, output_path.stat().st_size
