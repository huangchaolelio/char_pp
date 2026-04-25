"""Feature 015 — structured error code prefixes for pipeline_steps failures.

Usage:
    from src.services.kb_extraction_pipeline.error_codes import (
        VIDEO_QUALITY_REJECTED, format_error,
    )
    raise RuntimeError(format_error(VIDEO_QUALITY_REJECTED, "fps=12 vs 15"))

The prefix convention lets operations scripts grep for known failure modes
and map them to runbooks without parsing free-form error text.

Full list documented in ``specs/015-kb-pipeline-real-algorithms/data-model.md``
§ "错误码约定（FR-016）". Keep this module tightly aligned with that table.
"""

from __future__ import annotations


# ── Error code constants (prefix-only, no trailing colon) ────────────────────

#: Video fps / resolution below Feature-002 thresholds. Not retried.
VIDEO_QUALITY_REJECTED = "VIDEO_QUALITY_REJECTED"

#: ``estimate_pose`` returned an empty frame list. Not retried (CPU-bound).
POSE_NO_KEYPOINTS = "POSE_NO_KEYPOINTS"

#: ``estimate_pose`` backend failed to load (model file missing / CUDA unusable).
#: Tenacity I/O retry applies (treated as transient for model download cases).
POSE_MODEL_LOAD_FAILED = "POSE_MODEL_LOAD_FAILED"

#: Whisper model load or checkpoint download failed. Tenacity I/O retry applies.
WHISPER_LOAD_FAILED = "WHISPER_LOAD_FAILED"

#: Video has no usable audio track — executor returns ``skipped`` (not failed).
#: This prefix is used in the ``skip_reason`` payload, not in an exception.
WHISPER_NO_AUDIO = "WHISPER_NO_AUDIO"

#: ``action_segmenter`` / ``action_classifier`` produced nothing usable.
#: Not retried — inherent to video content, not transient.
ACTION_CLASSIFY_FAILED = "ACTION_CLASSIFY_FAILED"

#: Neither VENUS_TOKEN nor OPENAI_API_KEY is configured. Not retried.
LLM_UNCONFIGURED = "LLM_UNCONFIGURED"

#: LLM returned a response that cannot be parsed as the expected JSON schema.
#: Not retried — this is an output-format problem, not a transient network issue.
LLM_JSON_PARSE = "LLM_JSON_PARSE"

#: LLM HTTP call failed (5xx, timeout, connection reset, etc). Tenacity
#: retries based on the exception type (ConnectionError / TimeoutError).
LLM_CALL_FAILED = "LLM_CALL_FAILED"


# The complete set — used by tests to assert coverage of the documented table.
ALL_ERROR_CODES: frozenset[str] = frozenset({
    VIDEO_QUALITY_REJECTED,
    POSE_NO_KEYPOINTS,
    POSE_MODEL_LOAD_FAILED,
    WHISPER_LOAD_FAILED,
    WHISPER_NO_AUDIO,
    ACTION_CLASSIFY_FAILED,
    LLM_UNCONFIGURED,
    LLM_JSON_PARSE,
    LLM_CALL_FAILED,
})


def format_error(code: str, details: str) -> str:
    """Combine an error code constant with a human-readable detail string.

    The result always matches the pattern ``<CODE>: <details>`` so operations
    scripts can split on the first ``": "`` separator to get the code portion
    without locale-dependent parsing.

    Args:
        code: One of the constants above (or any user-defined prefix).
        details: Free-form explanation, kept ASCII-safe when possible.

    Returns:
        A string like ``"VIDEO_QUALITY_REJECTED: fps=12 vs 15"``.
    """
    return f"{code}: {details}"
