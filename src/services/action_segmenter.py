"""Action segmenter — splits a pose sequence into discrete action clips.

Algorithm (from spec + plan.md):
  - Detect peaks in wrist keypoint velocity (landmarks 15/16)
  - Each peak = one stroke / hit event
  - Segment window: peak_timestamp ± 500 ms (0.5 s before and after)
  - Multiple overlapping windows are merged
"""

import logging
import math
from dataclasses import dataclass

from src.services.pose_estimator import (
    LANDMARK_LEFT_WRIST,
    LANDMARK_RIGHT_WRIST,
    FramePoseResult,
)

logger = logging.getLogger(__name__)

# Half-window around each detected peak (milliseconds)
_HALF_WINDOW_MS = 500

# Minimum distance between consecutive peaks (ms) — avoids double-counting
_MIN_PEAK_SEPARATION_MS = 300

# Velocity threshold multiplier over the mean to count as a peak
_PEAK_THRESHOLD_FACTOR = 1.5


@dataclass
class ActionSegment:
    start_ms: int
    end_ms: int
    peak_ms: int  # wrist velocity peak (approximate ball-contact moment)
    peak_frame_index: int


def _wrist_velocity(
    frame: FramePoseResult,
    prev_frame: FramePoseResult,
) -> float:
    """Euclidean speed of the dominant wrist between two consecutive frames."""
    for wrist_idx in (LANDMARK_LEFT_WRIST, LANDMARK_RIGHT_WRIST):
        kp_curr = frame.keypoints.get(wrist_idx)
        kp_prev = prev_frame.keypoints.get(wrist_idx)
        if kp_curr and kp_prev:
            dt_ms = frame.timestamp_ms - prev_frame.timestamp_ms
            if dt_ms <= 0:
                continue
            dx = kp_curr.x - kp_prev.x
            dy = kp_curr.y - kp_prev.y
            speed = math.hypot(dx, dy) / (dt_ms / 1000.0)
            return speed
    return 0.0


def segment_actions(frames: list[FramePoseResult]) -> list[ActionSegment]:
    """Detect action segments from a pose-estimated frame sequence.

    Args:
        frames: Ordered list of FramePoseResult from pose_estimator.

    Returns:
        List of ActionSegment sorted by start_ms.
    """
    if len(frames) < 2:
        return []

    # Step 1: compute per-frame wrist velocities
    velocities: list[tuple[int, int, float]] = []  # (frame_index, timestamp_ms, velocity)
    for i in range(1, len(frames)):
        v = _wrist_velocity(frames[i], frames[i - 1])
        velocities.append((i, frames[i].timestamp_ms, v))

    if not velocities:
        return []

    # Step 2: find peaks above threshold
    mean_v = sum(v for _, _, v in velocities) / len(velocities)
    threshold = mean_v * _PEAK_THRESHOLD_FACTOR
    logger.debug("Velocity mean=%.4f threshold=%.4f", mean_v, threshold)

    peaks: list[tuple[int, int]] = []  # (frame_index, timestamp_ms)
    last_peak_ms = -_MIN_PEAK_SEPARATION_MS * 2

    for frame_idx, ts_ms, v in velocities:
        if v >= threshold and (ts_ms - last_peak_ms) >= _MIN_PEAK_SEPARATION_MS:
            peaks.append((frame_idx, ts_ms))
            last_peak_ms = ts_ms
            logger.debug("Peak detected: frame=%d ts=%dms v=%.4f", frame_idx, ts_ms, v)

    # Step 3: build segments centered on each peak
    max_ts = frames[-1].timestamp_ms
    segments: list[ActionSegment] = []
    for frame_idx, peak_ms in peaks:
        start_ms = max(0, peak_ms - _HALF_WINDOW_MS)
        end_ms = min(max_ts, peak_ms + _HALF_WINDOW_MS)
        segments.append(
            ActionSegment(
                start_ms=start_ms,
                end_ms=end_ms,
                peak_ms=peak_ms,
                peak_frame_index=frame_idx,
            )
        )

    logger.info("Action segmentation: %d peaks → %d segments", len(peaks), len(segments))
    return sorted(segments, key=lambda s: s.start_ms)


def frames_for_segment(
    frames: list[FramePoseResult],
    segment: ActionSegment,
) -> list[FramePoseResult]:
    """Return the subset of frames that fall within *segment*'s time window."""
    return [
        f for f in frames
        if segment.start_ms <= f.timestamp_ms <= segment.end_ms
    ]
