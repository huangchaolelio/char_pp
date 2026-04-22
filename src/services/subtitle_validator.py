"""SubtitleValidator — validates SRT subtitle sync against Whisper transcript.

Responsibilities:
- Parse SRT subtitle files (no third-party deps, pure regex)
- Extract embedded subtitle streams from video via ffmpeg
- Cross-validate subtitle timestamps against Whisper transcript timestamps
- Report sync status and fallback reason suffix for audio_fallback_reason field
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# SRT timecode pattern: HH:MM:SS,mmm --> HH:MM:SS,mmm
_SRT_TIMECODE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)

# Minimum character-overlap ratio to consider two sentences as matching the same content
_MIN_OVERLAP_RATIO = 0.5

# Default sync threshold in seconds
_DEFAULT_SYNC_THRESHOLD_S = 2.0


def _timecode_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    """Convert SRT timecode components to seconds."""
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _char_overlap_ratio(a: str, b: str) -> float:
    """Compute character-set Jaccard similarity between two strings."""
    set_a = set(a.strip())
    set_b = set(b.strip())
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


@dataclass
class SubtitleValidationResult:
    """Result of subtitle vs transcript sync validation."""

    is_valid: bool
    """True if subtitles are in sync (or cannot be compared). False if desync detected."""

    fallback_suffix: str | None
    """Suffix to append to audio_fallback_reason (None if valid).
    Examples:
        "subtitle_out_of_sync: 3.2s"
        "subtitle_unsupported_format: not_srt"
    """

    max_offset_s: float
    """Maximum timestamp offset found across matched sentence pairs. 0.0 if no pairs found."""


class SubtitleValidator:
    """Validates subtitle timestamps against Whisper transcript timestamps."""

    SYNC_THRESHOLD_S: float = _DEFAULT_SYNC_THRESHOLD_S

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_srt(srt_path: Path) -> list[dict]:
        """Parse an SRT file and return a list of subtitle entries.

        Args:
            srt_path: Path to the .srt file.

        Returns:
            List of dicts with keys ``start_s``, ``end_s``, ``text``.
            Returns an empty list if the file has no valid SRT timecodes
            (caller treats empty list as unsupported format).
        """
        try:
            content = Path(srt_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read SRT file %s: %s", srt_path, exc)
            return []

        entries: list[dict] = []
        # Split into blocks separated by blank lines
        blocks = re.split(r"\n{2,}", content.strip())
        for block in blocks:
            lines = block.strip().splitlines()
            if not lines:
                continue
            # Find timecode line (may not be the first line in malformed SRTs)
            timecode_line = None
            text_lines: list[str] = []
            timecode_idx = -1
            for idx, line in enumerate(lines):
                m = _SRT_TIMECODE_RE.search(line)
                if m and timecode_line is None:
                    timecode_line = m
                    timecode_idx = idx
                elif timecode_idx >= 0:
                    text_lines.append(line)

            if timecode_line is None:
                continue

            g = timecode_line.groups()
            start_s = _timecode_to_seconds(g[0], g[1], g[2], g[3])
            end_s = _timecode_to_seconds(g[4], g[5], g[6], g[7])
            text = " ".join(text_lines).strip()
            if text:
                entries.append({"start_s": start_s, "end_s": end_s, "text": text})

        logger.debug("Parsed %d SRT entries from %s", len(entries), srt_path)
        return entries

    @staticmethod
    def extract_embedded_srt(video_path: Path, output_srt: Path) -> bool:
        """Extract the first embedded subtitle stream from a video to an SRT file.

        Uses ffmpeg: ``ffmpeg -i video -map 0:s:0 -c:s srt output.srt``

        Args:
            video_path: Source video file path.
            output_srt: Destination .srt file path.

        Returns:
            True if extraction succeeded and file is non-empty.
            False if no subtitle stream exists, ffmpeg is unavailable, or output is empty.
        """
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-map", "0:s:0",
            "-c:s", "srt",
            str(output_srt),
        ]
        logger.debug("Extracting embedded subtitles: %s → %s", video_path.name, output_srt.name)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            logger.debug("ffmpeg not found — subtitle extraction skipped")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg subtitle extraction timed out for %s", video_path)
            return False

        if result.returncode != 0:
            logger.debug(
                "No embedded subtitle stream in %s (ffmpeg rc=%d)",
                video_path.name, result.returncode,
            )
            return False

        if not output_srt.exists() or output_srt.stat().st_size == 0:
            logger.debug("ffmpeg produced empty SRT for %s", video_path.name)
            return False

        logger.info(
            "Embedded subtitles extracted: %s (%.1f KB)",
            output_srt.name, output_srt.stat().st_size / 1024,
        )
        return True

    @staticmethod
    def unsupported_format_suffix() -> str:
        """Return the fallback suffix for unsupported subtitle format."""
        return "subtitle_unsupported_format: not_srt"

    # ------------------------------------------------------------------
    # Core validation
    # ------------------------------------------------------------------

    def validate(
        self,
        transcript_sentences: list[dict],
        srt_sentences: list[dict],
    ) -> SubtitleValidationResult:
        """Cross-validate subtitle timestamps against Whisper transcript timestamps.

        Matching algorithm:
        1. For each subtitle sentence, find the Whisper sentence with the
           highest character-overlap ratio (Jaccard on char sets).
        2. If the best overlap ratio >= _MIN_OVERLAP_RATIO, record the pair
           and compute |subtitle.start_s - transcript.start|.
        3. max_offset_s = max offset across all matched pairs.
        4. is_valid = (max_offset_s <= SYNC_THRESHOLD_S).
        5. If no pairs are found (completely different content), treat as valid
           (conservative: avoid false positives).

        Args:
            transcript_sentences: Whisper output sentences, each a dict with
                ``start``, ``end``, ``text`` keys.
            srt_sentences: Parsed SRT entries, each with ``start_s``, ``end_s``,
                ``text`` keys.

        Returns:
            SubtitleValidationResult with is_valid, fallback_suffix, max_offset_s.
        """
        if not srt_sentences or not transcript_sentences:
            logger.debug("Subtitle validation skipped: empty sentences (srt=%d, transcript=%d)",
                         len(srt_sentences), len(transcript_sentences))
            return SubtitleValidationResult(is_valid=True, fallback_suffix=None, max_offset_s=0.0)

        offsets: list[float] = []

        for sub in srt_sentences:
            best_ratio = 0.0
            best_offset = None

            for trans in transcript_sentences:
                ratio = _char_overlap_ratio(sub["text"], trans["text"])
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_offset = abs(sub["start_s"] - trans["start"])

            if best_ratio >= _MIN_OVERLAP_RATIO and best_offset is not None:
                offsets.append(best_offset)

        if not offsets:
            # No matching pairs found → cannot determine sync → conservative valid
            logger.debug("No matching subtitle/transcript pairs found — treating as synced")
            return SubtitleValidationResult(is_valid=True, fallback_suffix=None, max_offset_s=0.0)

        max_offset_s = max(offsets)
        logger.info(
            "Subtitle sync check: max_offset=%.2fs (threshold=%.1fs) — %s",
            max_offset_s, self.SYNC_THRESHOLD_S,
            "OK" if max_offset_s <= self.SYNC_THRESHOLD_S else "OUT_OF_SYNC",
        )

        if max_offset_s > self.SYNC_THRESHOLD_S:
            suffix = f"subtitle_out_of_sync: {max_offset_s:.1f}s"
            return SubtitleValidationResult(
                is_valid=False,
                fallback_suffix=suffix,
                max_offset_s=max_offset_s,
            )

        return SubtitleValidationResult(is_valid=True, fallback_suffix=None, max_offset_s=max_offset_s)
