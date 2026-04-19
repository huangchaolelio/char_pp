"""Expert tech point extractor — derives standard parameter ranges from pose sequences.

For each action segment, computes 4 technical dimensions:
  1. elbow_angle      (°)   — mean elbow joint angle during the stroke
  2. swing_trajectory (ratio) — normalized wrist arc length relative to body height
  3. contact_timing   (ms)  — time offset from segment start to peak wrist velocity
  4. weight_transfer  (ratio) — hip lateral shift normalized to shoulder width

Extraction confidence:
  - Per-dimension confidence = mean visibility of the keypoints involved
  - Dimensions with confidence < 0.7 are NOT written to the knowledge base
    (enforced by the validation check before insertion)
"""

from __future__ import annotations


import logging
import math
from dataclasses import dataclass

from src.services.action_classifier import ClassifiedSegment
from src.services.action_segmenter import frames_for_segment
from src.services.pose_estimator import (
    LANDMARK_LEFT_ELBOW,
    LANDMARK_LEFT_HIP,
    LANDMARK_LEFT_SHOULDER,
    LANDMARK_LEFT_WRIST,
    LANDMARK_RIGHT_ELBOW,
    LANDMARK_RIGHT_HIP,
    LANDMARK_RIGHT_SHOULDER,
    LANDMARK_RIGHT_WRIST,
    FramePoseResult,
)

logger = logging.getLogger(__name__)


@dataclass
class TechDimension:
    dimension: str
    param_min: float
    param_max: float
    param_ideal: float
    unit: str
    extraction_confidence: float


@dataclass
class ExtractionResult:
    action_type: str
    dimensions: list[TechDimension]  # only dims with confidence >= threshold
    segment_start_ms: int
    segment_end_ms: int


def _safe_mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _angle_at_elbow(
    frames: list[FramePoseResult],
) -> tuple[list[float], list[float]]:
    """Returns (angle_values, confidences) across all frames."""
    angles, confs = [], []
    for frame in frames:
        for s_idx, e_idx, w_idx in [
            (LANDMARK_RIGHT_SHOULDER, LANDMARK_RIGHT_ELBOW, LANDMARK_RIGHT_WRIST),
            (LANDMARK_LEFT_SHOULDER, LANDMARK_LEFT_ELBOW, LANDMARK_LEFT_WRIST),
        ]:
            s = frame.keypoints.get(s_idx)
            e = frame.keypoints.get(e_idx)
            w = frame.keypoints.get(w_idx)
            if s and e and w:
                ax, ay = s.x - e.x, s.y - e.y
                cx, cy = w.x - e.x, w.y - e.y
                dot = ax * cx + ay * cy
                mag = math.hypot(ax, ay) * math.hypot(cx, cy)
                if mag > 1e-9:
                    angle = math.degrees(math.acos(max(-1.0, min(1.0, dot / mag))))
                    angles.append(angle)
                    confs.append(min(s.visibility, e.visibility, w.visibility))
                break
    return angles, confs


def _wrist_arc_ratio(
    frames: list[FramePoseResult],
) -> tuple[float | None, float]:
    """Wrist arc length normalized by shoulder width. Returns (ratio, confidence)."""
    arc_lengths, shoulder_widths, confs = [], [], []
    for i in range(1, len(frames)):
        for w_idx in (LANDMARK_RIGHT_WRIST, LANDMARK_LEFT_WRIST):
            kp_c = frames[i].keypoints.get(w_idx)
            kp_p = frames[i - 1].keypoints.get(w_idx)
            if kp_c and kp_p:
                arc_lengths.append(math.hypot(kp_c.x - kp_p.x, kp_c.y - kp_p.y))
                confs.append(min(kp_c.visibility, kp_p.visibility))
                break
    for frame in frames:
        ls = frame.keypoints.get(LANDMARK_LEFT_SHOULDER)
        rs = frame.keypoints.get(LANDMARK_RIGHT_SHOULDER)
        if ls and rs:
            shoulder_widths.append(math.hypot(ls.x - rs.x, ls.y - rs.y))
    total_arc = sum(arc_lengths)
    mean_sw = _safe_mean(shoulder_widths) or 0.1
    ratio = total_arc / mean_sw if mean_sw > 1e-9 else None
    conf = _safe_mean(confs) or 0.0
    return ratio, conf


def _contact_timing_ms(
    frames: list[FramePoseResult],
    segment_start_ms: int,
) -> tuple[float | None, float]:
    """Time from segment start to peak wrist velocity (ms). Returns (timing, confidence)."""
    if len(frames) < 2:
        return None, 0.0
    best_v, best_ts, confs = 0.0, frames[0].timestamp_ms, []
    for i in range(1, len(frames)):
        for w_idx in (LANDMARK_RIGHT_WRIST, LANDMARK_LEFT_WRIST):
            kp_c = frames[i].keypoints.get(w_idx)
            kp_p = frames[i - 1].keypoints.get(w_idx)
            if kp_c and kp_p:
                dt = (frames[i].timestamp_ms - frames[i - 1].timestamp_ms) / 1000.0
                if dt > 0:
                    v = math.hypot(kp_c.x - kp_p.x, kp_c.y - kp_p.y) / dt
                    confs.append(min(kp_c.visibility, kp_p.visibility))
                    if v > best_v:
                        best_v, best_ts = v, frames[i].timestamp_ms
                break
    timing = float(best_ts - segment_start_ms)
    conf = _safe_mean(confs) or 0.0
    return timing, conf


def _weight_transfer_ratio(
    frames: list[FramePoseResult],
) -> tuple[float | None, float]:
    """Hip lateral shift normalized by shoulder width. Returns (ratio, confidence)."""
    hip_shifts, confs = [], []
    for i in range(1, len(frames)):
        lh_c = frames[i].keypoints.get(LANDMARK_LEFT_HIP)
        rh_c = frames[i].keypoints.get(LANDMARK_RIGHT_HIP)
        lh_p = frames[i - 1].keypoints.get(LANDMARK_LEFT_HIP)
        rh_p = frames[i - 1].keypoints.get(LANDMARK_RIGHT_HIP)
        if lh_c and rh_c and lh_p and rh_p:
            mid_c = (lh_c.x + rh_c.x) / 2
            mid_p = (lh_p.x + rh_p.x) / 2
            hip_shifts.append(abs(mid_c - mid_p))
            confs.append(min(lh_c.visibility, rh_c.visibility, lh_p.visibility, rh_p.visibility))
    shoulder_widths = []
    for frame in frames:
        ls = frame.keypoints.get(LANDMARK_LEFT_SHOULDER)
        rs = frame.keypoints.get(LANDMARK_RIGHT_SHOULDER)
        if ls and rs:
            shoulder_widths.append(math.hypot(ls.x - rs.x, ls.y - rs.y))
    total_shift = sum(hip_shifts)
    mean_sw = _safe_mean(shoulder_widths) or 0.1
    ratio = total_shift / mean_sw if mean_sw > 1e-9 else None
    conf = _safe_mean(confs) or 0.0
    return ratio, conf


def extract_tech_points(
    classified: ClassifiedSegment,
    all_frames: list[FramePoseResult],
    confidence_threshold: float = 0.7,
) -> ExtractionResult:
    """Extract technical dimension parameters for one classified action segment.

    Args:
        classified: The classified action segment.
        all_frames: Full frame list from pose_estimator (used to slice the segment).
        confidence_threshold: Dimensions below this threshold are excluded.

    Returns:
        ExtractionResult containing only high-confidence TechDimension entries.
    """
    segment_frames = frames_for_segment(all_frames, classified.segment)
    dimensions: list[TechDimension] = []

    # ── 1. elbow_angle ────────────────────────────────────────────────────────
    angles, angle_confs = _angle_at_elbow(segment_frames)
    if angles:
        conf = _safe_mean(angle_confs) or 0.0
        if conf >= confidence_threshold:
            dimensions.append(TechDimension(
                dimension="elbow_angle",
                param_min=min(angles),
                param_max=max(angles),
                param_ideal=_safe_mean(angles),  # type: ignore[arg-type]
                unit="°",
                extraction_confidence=conf,
            ))

    # ── 2. swing_trajectory ───────────────────────────────────────────────────
    arc_ratio, arc_conf = _wrist_arc_ratio(segment_frames)
    if arc_ratio is not None and arc_conf >= confidence_threshold:
        dimensions.append(TechDimension(
            dimension="swing_trajectory",
            param_min=arc_ratio * 0.85,
            param_max=arc_ratio * 1.15,
            param_ideal=arc_ratio,
            unit="ratio",
            extraction_confidence=arc_conf,
        ))

    # ── 3. contact_timing ─────────────────────────────────────────────────────
    timing, timing_conf = _contact_timing_ms(segment_frames, classified.segment.start_ms)
    if timing is not None and timing_conf >= confidence_threshold:
        dimensions.append(TechDimension(
            dimension="contact_timing",
            param_min=max(0.0, timing - 100.0),
            param_max=timing + 100.0,
            param_ideal=timing,
            unit="ms",
            extraction_confidence=timing_conf,
        ))

    # ── 4. weight_transfer ────────────────────────────────────────────────────
    wt_ratio, wt_conf = _weight_transfer_ratio(segment_frames)
    if wt_ratio is not None and wt_conf >= confidence_threshold:
        dimensions.append(TechDimension(
            dimension="weight_transfer",
            param_min=wt_ratio * 0.8,
            param_max=wt_ratio * 1.2,
            param_ideal=wt_ratio,
            unit="ratio",
            extraction_confidence=wt_conf,
        ))

    logger.info(
        "Extracted %d/%d dimensions for %s segment [%dms-%dms]",
        len(dimensions), 4,
        classified.action_type,
        classified.segment.start_ms,
        classified.segment.end_ms,
    )
    return ExtractionResult(
        action_type=classified.action_type,
        dimensions=dimensions,
        segment_start_ms=classified.segment.start_ms,
        segment_end_ms=classified.segment.end_ms,
    )
