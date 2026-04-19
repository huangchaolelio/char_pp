"""Pose estimation wrapper — extracts per-frame keypoints from a video file.

Design decisions:
  - Backend auto-detected at runtime: YOLOv8-pose (GPU/CUDA) preferred, MediaPipe (CPU) fallback.
  - "auto": use YOLOv8 if torch.cuda.is_available() and ultralytics is installed, else MediaPipe.
  - "yolov8": force YOLOv8 (raises if ultralytics not installed).
  - "mediapipe": force MediaPipe (CPU-only).
  - Public interface is unchanged: estimate_pose(video_path) -> list[FramePoseResult].
  - YOLOv8 outputs COCO 17-keypoint format; remapped to MediaPipe indices so downstream is stable.
  - Keypoints with visibility < keypoint_visibility_threshold are set to None.
  - Frame-level confidence = mean visibility of present keypoints.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# MediaPipe landmark indices referenced elsewhere in the pipeline
LANDMARK_LEFT_WRIST = 15
LANDMARK_RIGHT_WRIST = 16
LANDMARK_LEFT_ELBOW = 13
LANDMARK_RIGHT_ELBOW = 14
LANDMARK_LEFT_SHOULDER = 11
LANDMARK_RIGHT_SHOULDER = 12
LANDMARK_LEFT_HIP = 23
LANDMARK_RIGHT_HIP = 24
LANDMARK_LEFT_KNEE = 25
LANDMARK_RIGHT_KNEE = 26

# COCO-17 index → MediaPipe index mapping (only landmarks used downstream)
# COCO: 5=left_shoulder, 6=right_shoulder, 7=left_elbow, 8=right_elbow,
#        9=left_wrist, 10=right_wrist, 11=left_hip, 12=right_hip,
#       13=left_knee, 14=right_knee
_COCO_TO_MEDIAPIPE: dict = {
    5: LANDMARK_LEFT_SHOULDER,
    6: LANDMARK_RIGHT_SHOULDER,
    7: LANDMARK_LEFT_ELBOW,
    8: LANDMARK_RIGHT_ELBOW,
    9: LANDMARK_LEFT_WRIST,
    10: LANDMARK_RIGHT_WRIST,
    11: LANDMARK_LEFT_HIP,
    12: LANDMARK_RIGHT_HIP,
    13: LANDMARK_LEFT_KNEE,
    14: LANDMARK_RIGHT_KNEE,
}


@dataclass
class Keypoint:
    x: float  # normalized [0, 1]
    y: float
    z: float
    visibility: float


@dataclass
class FramePoseResult:
    frame_index: int
    timestamp_ms: int
    # Keypoints indexed by MediaPipe landmark index; None = below visibility threshold
    keypoints: dict = field(default_factory=dict)  # int -> Optional[Keypoint]
    frame_confidence: float = 0.0  # mean visibility of present keypoints


def _detect_backend(requested: str) -> str:
    """Resolve 'auto' to a concrete backend name."""
    if requested == "mediapipe":
        return "mediapipe"
    if requested == "yolov8":
        return "yolov8"
    # auto: prefer YOLOv8 if CUDA + ultralytics are available
    try:
        import torch  # type: ignore[import]
        import ultralytics  # type: ignore[import]  # noqa: F401
        if torch.cuda.is_available():
            logger.info("GPU detected (%s) — using YOLOv8-pose backend", torch.cuda.get_device_name(0))
            return "yolov8"
    except ImportError:
        pass
    logger.info("No GPU / ultralytics not installed — using MediaPipe backend")
    return "mediapipe"


def _estimate_pose_yolov8(
    video_path: Path,
    visibility_threshold: float,
    batch_size: int,
) -> list[FramePoseResult]:
    """Run YOLOv8-pose on *video_path* using GPU inference."""
    try:
        import cv2  # type: ignore[import]
        from ultralytics import YOLO  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("ultralytics or opencv-python-headless is not installed") from exc

    model = YOLO("yolov8n-pose.pt")  # downloads on first use; ~7 MB

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # Collect all frames first, then infer in batches for GPU efficiency
    frames: list[tuple[int, int, object]] = []  # (frame_index, timestamp_ms, bgr_frame)
    frame_index = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        timestamp_ms = int(frame_index * 1000 / fps)
        frames.append((frame_index, timestamp_ms, frame))
        frame_index += 1
    cap.release()

    results: list[FramePoseResult] = []

    for batch_start in range(0, len(frames), batch_size):
        batch = frames[batch_start : batch_start + batch_size]
        bgr_batch = [f[2] for f in batch]

        # ultralytics accepts a list of numpy arrays
        yolo_results = model(bgr_batch, verbose=False)

        for (fidx, ts_ms, _), yolo_res in zip(batch, yolo_results):
            keypoints: dict = {}
            confidences: list[float] = []

            # yolo_res.keypoints: shape [num_persons, 17, 3] (x, y, conf)
            if yolo_res.keypoints is not None and len(yolo_res.keypoints.data) > 0:
                # Take the highest-confidence person (first detection, sorted by box conf)
                kps = yolo_res.keypoints.data[0]  # shape [17, 3]
                img_h, img_w = yolo_res.orig_shape

                for coco_idx, mp_idx in _COCO_TO_MEDIAPIPE.items():
                    if coco_idx >= len(kps):
                        keypoints[mp_idx] = None
                        continue
                    x_px, y_px, conf = float(kps[coco_idx][0]), float(kps[coco_idx][1]), float(kps[coco_idx][2])
                    if conf >= visibility_threshold:
                        kp = Keypoint(
                            x=x_px / img_w if img_w > 0 else 0.0,
                            y=y_px / img_h if img_h > 0 else 0.0,
                            z=0.0,  # YOLOv8-pose does not output z
                            visibility=conf,
                        )
                        keypoints[mp_idx] = kp
                        confidences.append(conf)
                    else:
                        keypoints[mp_idx] = None

            frame_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            frame_result = FramePoseResult(
                frame_index=fidx,
                timestamp_ms=ts_ms,
                keypoints=keypoints,
                frame_confidence=frame_confidence,
            )
            results.append(frame_result)

            logger.debug(
                "frame=%d ts=%dms conf=%.3f visible_kps=%d [yolov8]",
                fidx, ts_ms, frame_confidence, len(confidences),
            )

    logger.info("YOLOv8 pose estimation complete: %d frames processed", len(results))
    return results


def _estimate_pose_mediapipe(
    video_path: Path,
    visibility_threshold: float,
    model_complexity: int,
) -> list[FramePoseResult]:
    """Run MediaPipe Pose on *video_path* (CPU inference)."""
    try:
        import cv2  # type: ignore[import]
        import mediapipe as mp  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("mediapipe or opencv-python-headless is not installed") from exc

    mp_pose = mp.solutions.pose  # type: ignore[attr-defined]
    results: list[FramePoseResult] = []

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    with mp_pose.Pose(
        static_image_mode=False,
        model_complexity=model_complexity,
        smooth_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as pose:
        frame_index = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            timestamp_ms = int(frame_index * 1000 / fps)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pose_result = pose.process(rgb_frame)

            keypoints: dict = {}
            confidences: list[float] = []

            if pose_result.pose_landmarks:
                for idx, lm in enumerate(pose_result.pose_landmarks.landmark):
                    if lm.visibility >= visibility_threshold:
                        kp = Keypoint(x=lm.x, y=lm.y, z=lm.z, visibility=lm.visibility)
                        keypoints[idx] = kp
                        confidences.append(lm.visibility)
                    else:
                        keypoints[idx] = None

            frame_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            frame_result = FramePoseResult(
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                keypoints=keypoints,
                frame_confidence=frame_confidence,
            )
            results.append(frame_result)

            logger.debug(
                "frame=%d ts=%dms conf=%.3f visible_kps=%d [mediapipe]",
                frame_index, timestamp_ms, frame_confidence, len(confidences),
            )

            frame_index += 1

    cap.release()
    logger.info("MediaPipe pose estimation complete: %d frames processed", len(results))
    return results


def estimate_pose(video_path: Path) -> list[FramePoseResult]:
    """Run pose estimation on every frame of *video_path*.

    Backend is selected based on ``settings.pose_backend``:
    - ``"auto"`` (default): YOLOv8-pose on GPU if CUDA + ultralytics available, else MediaPipe.
    - ``"yolov8"``: Force YOLOv8-pose (requires ``pip install ultralytics``).
    - ``"mediapipe"``: Force MediaPipe (CPU-only).

    Args:
        video_path: Local path to a validated video file.

    Returns:
        List of FramePoseResult, one per frame. Keypoints are indexed by MediaPipe landmark
        index regardless of backend. Keypoints below visibility threshold are None.
    """
    from src.config import get_settings
    settings = get_settings()

    backend = _detect_backend(settings.pose_backend)

    if backend == "yolov8":
        return _estimate_pose_yolov8(
            video_path,
            visibility_threshold=settings.keypoint_visibility_threshold,
            batch_size=settings.pose_batch_size,
        )
    else:
        return _estimate_pose_mediapipe(
            video_path,
            visibility_threshold=settings.keypoint_visibility_threshold,
            model_complexity=settings.mediapipe_model_complexity,
        )
