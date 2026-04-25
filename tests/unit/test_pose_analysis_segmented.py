"""Feature-016 US2 / T028 — pose_analysis over preprocessed segments.

Asserts the post-US2 executor:
- Iterates ``segments/seg_NNNN.mp4`` from the upstream download_video output dir
  in segment_index order;
- Accumulates frames across all segments with ``timestamp_ms`` rebased onto the
  original-video timeline (seg.start_ms offset added);
- Reports ``segments_processed`` / ``segments_failed`` in output_summary;
- Still writes a single ``pose.json`` via the existing artifact_io helper.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.kb_extraction_pipeline.step_executors import pose_analysis


pytestmark = pytest.mark.asyncio


class _FakeFrame:
    """Minimal stand-in for ``FramePoseResult`` — only fields artifact_io reads."""

    def __init__(self, timestamp_ms: int):
        self.timestamp_ms = timestamp_ms
        self.frame_index = 0
        self.frame_confidence = 1.0
        self.keypoints = {}  # dict[int, Keypoint] — empty is fine
        self.bbox = None
        self.confidence = 1.0


class _FakeVideoMeta:
    def __init__(self, duration_seconds: float, fps: float = 30.0):
        self.fps = fps
        self.width = 1280
        self.height = 720
        self.duration_seconds = duration_seconds
        self.frame_count = int(duration_seconds * fps)


async def test_pose_analysis_iterates_segments_in_order(tmp_path, monkeypatch):
    """3 segments → 3 × estimate_pose calls in order; frames accumulated."""
    # Build a fake download_video artifact directory.
    job_id = uuid4()
    download_dir = tmp_path / str(job_id)
    seg_dir = download_dir / "segments"
    seg_dir.mkdir(parents=True)
    for i in range(3):
        (seg_dir / f"seg_{i:04d}.mp4").write_bytes(b"fake_mp4_data")

    # Monkeypatch _get_video_path → returns the download_dir (not a file).
    async def fake_get_path(session, job, step_id=None):
        return str(download_dir)

    monkeypatch.setattr(pose_analysis, "_get_video_path", fake_get_path)

    # Monkeypatch preprocessing_service to return segment timing metadata.
    segs_meta = [
        SimpleNamespace(segment_index=0, start_ms=0, end_ms=180_000),
        SimpleNamespace(segment_index=1, start_ms=180_000, end_ms=360_000),
        SimpleNamespace(segment_index=2, start_ms=360_000, end_ms=540_000),
    ]
    async def fake_load_view(session, cos_object_key):
        return SimpleNamespace(
            segments=segs_meta,
            original_meta={
                "fps": 30.0, "width": 1280, "height": 720,
                "duration_ms": 540_000, "codec": "h264",
                "size_bytes": 1, "has_audio": True,
            },
        )
    monkeypatch.setattr(
        pose_analysis, "_load_preprocessing_view", fake_load_view, raising=False,
    )

    calls: list[Path] = []

    def fake_estimate(video_path):
        """Each segment produces 2 frames with local-relative timestamps."""
        calls.append(Path(video_path))
        return [_FakeFrame(0), _FakeFrame(1000)]

    monkeypatch.setattr(pose_analysis.pose_estimator, "estimate_pose", fake_estimate)
    monkeypatch.setattr(
        pose_analysis.pose_estimator, "_detect_backend", lambda req: "yolov8", raising=False,
    )
    monkeypatch.setattr(
        pose_analysis.video_validator, "validate_video",
        lambda p: _FakeVideoMeta(540.0), raising=True,
    )

    # Build fake job & step.
    job = SimpleNamespace(
        id=job_id,
        cos_object_key="x/y/z.mp4",
        enable_audio_analysis=True,
    )
    step = SimpleNamespace(id=uuid4(), output_artifact_path=None)

    session = SimpleNamespace()  # unused — monkeypatched helpers bypass it

    result = await pose_analysis.execute(session, job, step)

    # ── Assertions ────────────────────────────────────────────────────────
    # 3 segments processed in order.
    assert len(calls) == 3
    assert [p.name for p in calls] == [
        "seg_0000.mp4", "seg_0001.mp4", "seg_0002.mp4",
    ]

    # output_summary reports segments processed.
    assert result["status"].value == "success" or result["status"] == "success"
    summary = result["output_summary"]
    assert summary["segments_processed"] == 3
    assert summary["segments_failed"] == 0
    assert summary["keypoints_frame_count"] == 6  # 2 frames × 3 segments

    # pose.json written with rebased timestamps.
    pose_path = Path(result["output_artifact_path"])
    assert pose_path.exists()
    payload = json.loads(pose_path.read_text())
    ts = [f["timestamp_ms"] for f in payload["frames"]]
    # seg0: [0, 1000]  seg1: [180000, 181000]  seg2: [360000, 361000]
    assert ts == [0, 1000, 180_000, 181_000, 360_000, 361_000]
