"""Unit tests — Feature 015 artifact serialization helpers (T005).

Covers FR-002 / FR-007 / spec Q4: readers tolerate missing, extra and
malformed fields so executors never crash on schema drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.kb_extraction_pipeline.artifact_io import (
    read_pose_artifact,
    read_transcript_artifact,
    write_pose_artifact,
    write_transcript_artifact,
)
from src.services.pose_estimator import FramePoseResult, Keypoint


pytestmark = pytest.mark.unit


# ── pose.json round-trip ────────────────────────────────────────────────────


class TestPoseArtifactRoundTrip:
    def test_write_then_read_roundtrip_preserves_frames(self, tmp_path) -> None:
        path = tmp_path / "pose.json"
        frames = [
            FramePoseResult(
                frame_index=0,
                timestamp_ms=0,
                frame_confidence=0.95,
                keypoints={
                    0: Keypoint(x=0.5, y=0.3, z=0.0, visibility=0.95),
                    11: Keypoint(x=0.45, y=0.5, z=0.0, visibility=0.88),
                },
            ),
            FramePoseResult(
                frame_index=1,
                timestamp_ms=33,
                frame_confidence=0.92,
                keypoints={12: Keypoint(x=0.55, y=0.5, z=0.0, visibility=0.85)},
            ),
        ]
        write_pose_artifact(
            path,
            video_path="/tmp/video.mp4",
            video_meta={"fps": 30.0, "width": 1920, "height": 1080},
            backend="yolov8",
            frames=frames,
        )

        video_meta, backend, roundtrip = read_pose_artifact(path)
        assert video_meta == {"fps": 30.0, "width": 1920, "height": 1080}
        assert backend == "yolov8"
        assert len(roundtrip) == 2
        assert roundtrip[0].frame_index == 0
        assert roundtrip[0].keypoints[0].x == 0.5
        assert roundtrip[0].keypoints[11].visibility == 0.88
        assert roundtrip[1].keypoints[12].y == 0.5


class TestPoseArtifactTolerantRead:
    def test_missing_top_level_keys_use_defaults(self, tmp_path) -> None:
        """Q4: reader never crashes on missing keys."""
        path = tmp_path / "pose.json"
        path.write_text("{}")  # completely empty payload

        video_meta, backend, frames = read_pose_artifact(path)
        assert video_meta == {}
        assert backend == "unknown"
        assert frames == []

    def test_unknown_extra_keys_ignored(self, tmp_path) -> None:
        path = tmp_path / "pose.json"
        path.write_text(json.dumps({
            "video_meta": {"fps": 30.0},
            "backend": "mediapipe",
            "frames": [],
            "schema_version": "1.0",              # future unknown key
            "mystery_field": {"nested": [1, 2]},  # totally unrelated
        }))
        video_meta, backend, frames = read_pose_artifact(path)
        assert video_meta == {"fps": 30.0}
        assert backend == "mediapipe"
        assert frames == []

    def test_malformed_frames_skipped_not_raised(self, tmp_path) -> None:
        path = tmp_path / "pose.json"
        path.write_text(json.dumps({
            "frames": [
                {"frame_index": 0, "timestamp_ms": 0, "keypoints": {}},  # OK
                "not-a-dict",                                             # skip
                {"frame_index": 1, "timestamp_ms": 33, "keypoints": {
                    "11": {"x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.9},
                    "not-an-int": {"x": 0.0, "y": 0.0, "z": 0.0, "visibility": 0.0},  # skip this kp
                }},
            ],
        }))
        _, _, frames = read_pose_artifact(path)
        assert len(frames) == 2
        assert 11 in frames[1].keypoints

    def test_missing_file_returns_defaults_without_crash(self, tmp_path) -> None:
        path = tmp_path / "missing.json"
        video_meta, backend, frames = read_pose_artifact(path)
        assert video_meta == {}
        assert backend == "unknown"
        assert frames == []

    def test_malformed_json_returns_defaults(self, tmp_path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{ this is not json")
        video_meta, backend, frames = read_pose_artifact(path)
        assert video_meta == {}
        assert backend == "unknown"
        assert frames == []


# ── transcript.json round-trip ──────────────────────────────────────────────


class TestTranscriptArtifactRoundTrip:
    def test_write_then_read_preserves_sentences(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "transcript.json"

        # Build a minimal TranscriptResult-like object; avoid importing
        # AudioQualityFlag in the test to match the spec "no version pinning"
        # ethos — the writer stringifies whatever we pass.
        from src.services.speech_recognizer import TranscriptResult
        from src.models.audio_transcript import AudioQualityFlag

        tr = TranscriptResult(
            language="zh",
            model_version="whisper-small-20231117",
            total_duration_s=10.5,
            snr_db=12.3,
            quality_flag=AudioQualityFlag.ok,
            fallback_reason=None,
            sentences=[
                {"start": 0.0, "end": 3.2, "text": "肘部保持 90 到 120 度", "confidence": 0.89},
            ],
        )
        write_transcript_artifact(
            path,
            video_path="/tmp/video.mp4",
            audio_path="/tmp/audio.wav",
            transcript_result=tr,
        )

        result = read_transcript_artifact(path)
        assert result["language"] == "zh"
        assert result["model_version"] == "whisper-small-20231117"
        assert result["quality_flag"] == "ok"
        assert len(result["sentences"]) == 1
        assert result["sentences"][0]["text"] == "肘部保持 90 到 120 度"


class TestTranscriptArtifactTolerantRead:
    def test_missing_sentences_returns_empty_list(self, tmp_path) -> None:
        path = tmp_path / "transcript.json"
        path.write_text("{}")
        result = read_transcript_artifact(path)
        assert result["sentences"] == []
        assert result["language"] == "unknown"
        assert result["model_version"] == "unknown"
        assert result["quality_flag"] == "unknown"

    def test_non_list_sentences_coerced_to_empty(self, tmp_path) -> None:
        path = tmp_path / "transcript.json"
        path.write_text(json.dumps({"sentences": "not-a-list"}))
        result = read_transcript_artifact(path)
        assert result["sentences"] == []

    def test_unknown_extra_keys_ignored(self, tmp_path) -> None:
        path = tmp_path / "transcript.json"
        path.write_text(json.dumps({
            "language": "zh",
            "sentences": [{"text": "hello", "start": 0.0, "end": 1.0}],
            "some_future_field": "ignored",
        }))
        result = read_transcript_artifact(path)
        assert result["language"] == "zh"
        assert result["sentences"][0]["text"] == "hello"
        assert "some_future_field" not in result

    def test_non_dict_sentence_items_dropped(self, tmp_path) -> None:
        path = tmp_path / "transcript.json"
        path.write_text(json.dumps({
            "sentences": [
                {"text": "valid", "start": 0.0, "end": 1.0},
                "string-not-dict",
                123,
                {"text": "another valid", "start": 1.0, "end": 2.0},
            ],
        }))
        result = read_transcript_artifact(path)
        assert len(result["sentences"]) == 2
        assert result["sentences"][0]["text"] == "valid"
        assert result["sentences"][1]["text"] == "another valid"

    def test_missing_file_returns_safe_defaults(self, tmp_path) -> None:
        path = tmp_path / "missing.json"
        result = read_transcript_artifact(path)
        assert result["sentences"] == []
        assert result["language"] == "unknown"
