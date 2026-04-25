"""Structured error-code prefixes for Feature-016 preprocessing pipeline.

Every failure path in preprocessing / KB-extraction-consumption MUST set
``error_message`` on the owning DB row to ``f"{CODE}: {detail}"`` so ops can
``grep`` the distribution by stage (SC-007).

The prefixes are intentionally flat (no enum class / subclassing) — they need
to survive JSON serialisation and ``error_message.startswith(CODE + ":")``
checks in both Python code and shell scripts.
"""

from __future__ import annotations


# ── Preprocessing pipeline (Feature-016 FR-011) ─────────────────────────────

VIDEO_DOWNLOAD_FAILED = "VIDEO_DOWNLOAD_FAILED"
VIDEO_PROBE_FAILED = "VIDEO_PROBE_FAILED"
VIDEO_QUALITY_REJECTED = "VIDEO_QUALITY_REJECTED"
VIDEO_CODEC_UNSUPPORTED = "VIDEO_CODEC_UNSUPPORTED"
VIDEO_TRANSCODE_FAILED = "VIDEO_TRANSCODE_FAILED"
VIDEO_SPLIT_FAILED = "VIDEO_SPLIT_FAILED"
VIDEO_UPLOAD_FAILED = "VIDEO_UPLOAD_FAILED"
AUDIO_EXTRACT_FAILED = "AUDIO_EXTRACT_FAILED"


# ── KB extraction consumption (Feature-016 FR-011a) ─────────────────────────

SEGMENT_MISSING = "SEGMENT_MISSING"
AUDIO_MISSING = "AUDIO_MISSING"


# ── Formatting helper ───────────────────────────────────────────────────────

def format_error(code: str, detail: str) -> str:
    """Return ``"{code}: {detail}"`` ready for ``error_message`` storage.

    Keeping this helper module-level (rather than a method) lets callers keep
    ``raise RuntimeError(format_error(VIDEO_PROBE_FAILED, str(exc)))`` on a
    single line.
    """
    return f"{code}: {detail}"
