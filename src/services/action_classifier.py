"""Action classifier v1 — rule-based classifier for table tennis strokes.

v1 scope (from spec clarifications): forehand_topspin + backhand_push only.
Any segment that doesn't match either rule is classified as 'unknown'.

Classification rules:
  forehand_topspin:
    - Dominant wrist moves from low-right to high-left (for right-handed player)
    - Elbow angle decreases then increases through the stroke (acceleration pattern)
    - Net wrist displacement direction: predominantly upward (dy < 0 in image coords)

  backhand_push:
    - Dominant wrist crosses the body midline (x increases left to right in image)
    - Elbow stays relatively bent and close to body
    - Net wrist displacement direction: predominantly forward/horizontal

These are heuristic rules calibrated for CPU-only inference without body-side
detection. They will be refined once benchmark data (docs/benchmarks/) is available.
"""

from __future__ import annotations


import logging
import math

from src.services.action_segmenter import ActionSegment
from src.services.pose_estimator import (
    LANDMARK_LEFT_ELBOW,
    LANDMARK_LEFT_SHOULDER,
    LANDMARK_LEFT_WRIST,
    LANDMARK_RIGHT_ELBOW,
    LANDMARK_RIGHT_SHOULDER,
    LANDMARK_RIGHT_WRIST,
    FramePoseResult,
    Keypoint,
)

logger = logging.getLogger(__name__)

# Minimum number of frames with visible wrist to attempt classification
_MIN_VALID_FRAMES = 3


def _angle_between(a: Keypoint, b: Keypoint, c: Keypoint) -> float:
    """Compute the angle at vertex *b* formed by rays b→a and b→c (degrees)."""
    ax, ay = a.x - b.x, a.y - b.y
    cx, cy = c.x - b.x, c.y - b.y
    dot = ax * cx + ay * cy
    mag_a = math.hypot(ax, ay)
    mag_c = math.hypot(cx, cy)
    if mag_a < 1e-9 or mag_c < 1e-9:
        return 0.0
    cos_angle = max(-1.0, min(1.0, dot / (mag_a * mag_c)))
    return math.degrees(math.acos(cos_angle))


def _dominant_wrist_trajectory(
    frames: list[FramePoseResult],
) -> tuple[float, float] | None:
    """Return (mean_dx, mean_dy) of dominant wrist across visible frames."""
    displacements: list[tuple[float, float]] = []
    for i in range(1, len(frames)):
        for wrist_idx in (LANDMARK_RIGHT_WRIST, LANDMARK_LEFT_WRIST):
            kp_curr = frames[i].keypoints.get(wrist_idx)
            kp_prev = frames[i - 1].keypoints.get(wrist_idx)
            if kp_curr and kp_prev:
                displacements.append((kp_curr.x - kp_prev.x, kp_curr.y - kp_prev.y))
                break
    if len(displacements) < _MIN_VALID_FRAMES:
        return None
    mean_dx = sum(d[0] for d in displacements) / len(displacements)
    mean_dy = sum(d[1] for d in displacements) / len(displacements)
    return mean_dx, mean_dy


def _mean_elbow_angle(frames: list[FramePoseResult]) -> float | None:
    """Compute mean elbow angle across frames where all 3 landmarks are visible."""
    angles: list[float] = []
    for frame in frames:
        for shoulder_idx, elbow_idx, wrist_idx in [
            (LANDMARK_RIGHT_SHOULDER, LANDMARK_RIGHT_ELBOW, LANDMARK_RIGHT_WRIST),
            (LANDMARK_LEFT_SHOULDER, LANDMARK_LEFT_ELBOW, LANDMARK_LEFT_WRIST),
        ]:
            s = frame.keypoints.get(shoulder_idx)
            e = frame.keypoints.get(elbow_idx)
            w = frame.keypoints.get(wrist_idx)
            if s and e and w:
                angles.append(_angle_between(s, e, w))
                break  # use first visible arm
    return sum(angles) / len(angles) if angles else None


class ClassifiedSegment:
    """An ActionSegment together with its classified action type."""

    def __init__(self, segment: ActionSegment, action_type: str) -> None:
        self.segment = segment
        self.action_type = action_type  # "forehand_topspin" | "backhand_push" | "unknown"

    def __repr__(self) -> str:
        return (
            f"ClassifiedSegment(action_type={self.action_type!r}, "
            f"start={self.segment.start_ms}ms, end={self.segment.end_ms}ms)"
        )


def classify_segment(
    segment_frames: list[FramePoseResult],
    segment: ActionSegment,
) -> ClassifiedSegment:
    """Classify a single action segment using rule-based heuristics.

    Args:
        segment_frames: Frames belonging to this segment (from action_segmenter).
        segment: The ActionSegment metadata.

    Returns:
        ClassifiedSegment with action_type set.
    """
    if len(segment_frames) < _MIN_VALID_FRAMES:
        logger.debug("Segment %dms-%dms: too few frames → unknown", segment.start_ms, segment.end_ms)
        return ClassifiedSegment(segment, "unknown")

    trajectory = _dominant_wrist_trajectory(segment_frames)
    if trajectory is None:
        logger.debug("Segment %dms-%dms: no wrist trajectory → unknown", segment.start_ms, segment.end_ms)
        return ClassifiedSegment(segment, "unknown")

    mean_dx, mean_dy = trajectory
    elbow_angle = _mean_elbow_angle(segment_frames)

    # forehand_topspin: wrist moves upward (dy < 0 in image coords where y=0 is top)
    # and elbow angle is relatively open (> 100°) during the swing
    is_upward = mean_dy < -0.005
    is_open_elbow = (elbow_angle is None) or (elbow_angle > 100.0)

    # backhand_push: wrist moves predominantly horizontal/forward
    # elbow stays bent (< 120°) and close to the body
    is_horizontal = abs(mean_dx) > abs(mean_dy) * 0.8
    is_bent_elbow = (elbow_angle is not None) and (elbow_angle <= 120.0)

    if is_upward and is_open_elbow:
        action_type = "forehand_topspin"
    elif is_horizontal and is_bent_elbow:
        action_type = "backhand_push"
    else:
        action_type = "unknown"

    logger.debug(
        "Segment %dms-%dms: dx=%.4f dy=%.4f elbow=%.1f° → %s",
        segment.start_ms, segment.end_ms,
        mean_dx, mean_dy,
        elbow_angle or -1.0,
        action_type,
    )
    return ClassifiedSegment(segment, action_type)
