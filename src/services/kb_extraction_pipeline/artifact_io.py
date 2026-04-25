"""Feature 015 — artifact serialization helpers for pipeline_steps.

Reads/writes the JSON artifacts that bridge sub-steps in the KB extraction DAG:

  - ``pose.json`` — written by ``pose_analysis``, consumed by ``visual_kb_extract``
  - ``transcript.json`` — written by ``audio_transcription``, consumed by ``audio_kb_extract``

Design choice (spec Q4): no schema version. Readers tolerate missing / extra
keys so schema drift between executor releases does not crash downstream steps.

The helpers are pure I/O: they do not call any algorithm modules and do not
touch the database. Each executor composes them with Feature-002 algorithms.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.services.pose_estimator import FramePoseResult, Keypoint
from src.services.speech_recognizer import TranscriptResult


logger = logging.getLogger(__name__)


# ── pose.json ───────────────────────────────────────────────────────────────


def write_pose_artifact(
    path: Path,
    *,
    video_path: str,
    video_meta: dict[str, Any],
    backend: str,
    frames: list[FramePoseResult],
) -> None:
    """Serialize pose estimation output to a JSON file.

    The shape is intentionally permissive — see data-model.md § "pose.json":
        {"video_path", "video_meta", "backend", "frames": [...]}
    """
    payload = {
        "video_path": str(video_path),
        "video_meta": video_meta,
        "backend": backend,
        "frames": [_frame_to_dict(f) for f in frames],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False))


def read_pose_artifact(
    path: Path,
) -> tuple[dict[str, Any], str, list[FramePoseResult]]:
    """Parse a pose.json artifact with tolerant defaults (FR-002 / spec Q4).

    Missing keys:
      - video_meta → {}
      - backend → "unknown"
      - frames → []

    Unknown extra keys at any level are ignored. Malformed frames (missing
    required int/str fields) are skipped with a DEBUG log, not raised, so
    downstream extractors stay robust to schema drift.
    """
    data = _load_json_safely(path)
    video_meta = data.get("video_meta") or {}
    backend = str(data.get("backend") or "unknown")
    raw_frames = data.get("frames") or []
    if not isinstance(raw_frames, list):
        raw_frames = []

    frames: list[FramePoseResult] = []
    for raw in raw_frames:
        if not isinstance(raw, dict):
            continue
        try:
            frames.append(_frame_from_dict(raw))
        except (TypeError, ValueError, KeyError) as exc:
            logger.debug("pose artifact: skip malformed frame %r: %s", raw, exc)
    return video_meta, backend, frames


def _frame_to_dict(frame: FramePoseResult) -> dict[str, Any]:
    return {
        "frame_index": frame.frame_index,
        "timestamp_ms": frame.timestamp_ms,
        "frame_confidence": frame.frame_confidence,
        # Keypoint indices are ints → serialise as strings for JSON compatibility.
        # pose_estimator assigns None for keypoints below the visibility threshold;
        # those are skipped so asdict() only ever runs on real dataclass instances.
        "keypoints": {
            str(idx): asdict(kp)
            for idx, kp in frame.keypoints.items()
            if kp is not None
        },
    }


def _frame_from_dict(raw: dict[str, Any]) -> FramePoseResult:
    keypoints: dict[int, Keypoint] = {}
    for idx_str, kp_dict in (raw.get("keypoints") or {}).items():
        if not isinstance(kp_dict, dict):
            continue
        try:
            idx = int(idx_str)
        except (TypeError, ValueError):
            continue
        keypoints[idx] = Keypoint(
            x=float(kp_dict.get("x", 0.0)),
            y=float(kp_dict.get("y", 0.0)),
            z=float(kp_dict.get("z", 0.0)),
            visibility=float(kp_dict.get("visibility", 0.0)),
        )
    return FramePoseResult(
        frame_index=int(raw.get("frame_index", 0)),
        timestamp_ms=int(raw.get("timestamp_ms", 0)),
        keypoints=keypoints,
        frame_confidence=float(raw.get("frame_confidence", 0.0)),
    )


# ── transcript.json ─────────────────────────────────────────────────────────


def write_transcript_artifact(
    path: Path,
    *,
    video_path: str,
    audio_path: str,
    transcript_result: TranscriptResult,
) -> None:
    """Serialize a ``TranscriptResult`` to a JSON file.

    We flatten the dataclass plus its enum ``quality_flag`` so readers can
    consume the file without re-importing Feature-002's enum types.
    """
    payload = {
        "video_path": str(video_path),
        "audio_path": str(audio_path),
        "language": transcript_result.language,
        "model_version": transcript_result.model_version,
        "total_duration_s": transcript_result.total_duration_s,
        "snr_db": transcript_result.snr_db,
        "quality_flag": (
            transcript_result.quality_flag.value
            if hasattr(transcript_result.quality_flag, "value")
            else str(transcript_result.quality_flag)
        ),
        "fallback_reason": transcript_result.fallback_reason,
        "sentences": list(transcript_result.sentences),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False))


def read_transcript_artifact(path: Path) -> dict[str, Any]:
    """Parse a transcript.json artifact as a plain dict.

    Downstream (``TranscriptTechParser``) accepts ``sentences`` as
    ``list[dict]`` directly, so there is no need to re-hydrate a
    ``TranscriptResult`` dataclass here. Missing keys default safely:

      - sentences → []
      - language → "unknown"
      - model_version → "unknown"
      - quality_flag → "unknown"
    """
    data = _load_json_safely(path)
    sentences = data.get("sentences")
    if not isinstance(sentences, list):
        sentences = []
    # Drop non-dict items so consumers can assume list[dict].
    sentences = [s for s in sentences if isinstance(s, dict)]
    return {
        "video_path": str(data.get("video_path") or ""),
        "audio_path": str(data.get("audio_path") or ""),
        "language": str(data.get("language") or "unknown"),
        "model_version": str(data.get("model_version") or "unknown"),
        "total_duration_s": data.get("total_duration_s"),
        "snr_db": data.get("snr_db"),
        "quality_flag": str(data.get("quality_flag") or "unknown"),
        "fallback_reason": data.get("fallback_reason"),
        "sentences": sentences,
    }


# ── shared ──────────────────────────────────────────────────────────────────


def _load_json_safely(path: Path) -> dict[str, Any]:
    """Read JSON from ``path`` returning {} on any read / parse failure.

    The DAG orchestrator fails the calling step when downstream cannot find
    the artifact it expected; this helper only covers "file exists but
    partially corrupt" — we surface {} so the downstream executor can
    decide whether to treat that as a degradation or a hard failure.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("artifact_io: file not found: %s", path)
        return {}
    except json.JSONDecodeError as exc:
        logger.warning("artifact_io: malformed JSON in %s: %s", path, exc)
        return {}
    except OSError as exc:
        logger.warning("artifact_io: I/O error reading %s: %s", path, exc)
        return {}
