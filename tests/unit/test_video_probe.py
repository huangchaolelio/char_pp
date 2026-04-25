"""Unit tests for Feature-016 preprocessing.video_probe.

probe_and_validate(local_path) must:
- Invoke ffprobe to extract metadata (fps / width / height / duration_ms /
  codec / size_bytes / has_audio).
- Call video_validator.validate_video as the quality gate (FR-002a).
- Map a VideoQualityRejected exception to a RuntimeError with
  ``VIDEO_QUALITY_REJECTED:`` prefix.
- Map an undecodable/ffprobe-non-zero exit to ``VIDEO_PROBE_FAILED:``.
- Map an explicit unsupported codec to ``VIDEO_CODEC_UNSUPPORTED:``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# Module-under-test is imported lazily inside tests so the test file itself
# can be collected even before the module exists (TDD staging).


def _stub_ffprobe(fps=30.0, width=1920, height=1080, duration=120.0,
                  codec="h264", size_bytes=5_000_000, has_audio=True):
    """Stub return value of ``subprocess.run`` for ffprobe JSON call."""
    streams = [{
        "codec_type": "video",
        "codec_name": codec,
        "width": width,
        "height": height,
        "r_frame_rate": f"{int(fps*1000)}/1000",
        "duration": str(duration),
    }]
    if has_audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    payload = {
        "streams": streams,
        "format": {"duration": str(duration), "size": str(size_bytes)},
    }
    class _CompletedProc:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""
    return _CompletedProc()


@pytest.mark.unit
class TestVideoProbeHappyPath:
    def test_returns_metadata_with_audio(self, tmp_path):
        from src.services.preprocessing import video_probe

        fake_video = tmp_path / "v.mp4"
        fake_video.write_bytes(b"\x00" * 128)

        with patch("subprocess.run", return_value=_stub_ffprobe()), \
             patch(
                 "src.services.preprocessing.video_probe.validate_video",
                 return_value=None,
             ):
            meta = video_probe.probe_and_validate(fake_video)

        assert meta.fps == pytest.approx(30.0, rel=1e-3)
        assert meta.width == 1920
        assert meta.height == 1080
        assert meta.duration_ms == 120000
        assert meta.codec == "h264"
        assert meta.size_bytes == 5_000_000
        assert meta.has_audio is True

    def test_no_audio_stream_sets_has_audio_false(self, tmp_path):
        from src.services.preprocessing import video_probe

        fake_video = tmp_path / "v.mp4"
        fake_video.write_bytes(b"\x00" * 128)

        with patch(
            "subprocess.run",
            return_value=_stub_ffprobe(has_audio=False),
        ), patch(
            "src.services.preprocessing.video_probe.validate_video",
            return_value=None,
        ):
            meta = video_probe.probe_and_validate(fake_video)
        assert meta.has_audio is False


@pytest.mark.unit
class TestVideoProbeErrorMapping:
    def test_ffprobe_nonzero_exit_raises_probe_failed(self, tmp_path):
        from src.services.preprocessing import video_probe

        fake_video = tmp_path / "bad.mp4"
        fake_video.write_bytes(b"broken")

        class _Failed:
            returncode = 1
            stdout = ""
            stderr = "Invalid data found"

        with patch("subprocess.run", return_value=_Failed()):
            with pytest.raises(RuntimeError, match=r"^VIDEO_PROBE_FAILED:"):
                video_probe.probe_and_validate(fake_video)

    def test_quality_rejected_maps_to_prefixed_error(self, tmp_path):
        from src.services import video_validator
        from src.services.preprocessing import video_probe

        fake_video = tmp_path / "low_fps.mp4"
        fake_video.write_bytes(b"\x00" * 64)

        with patch(
            "subprocess.run",
            return_value=_stub_ffprobe(fps=12.0),
        ), patch(
            "src.services.preprocessing.video_probe.validate_video",
            side_effect=video_validator.VideoQualityRejected("fps too low"),
        ):
            with pytest.raises(RuntimeError, match=r"^VIDEO_QUALITY_REJECTED:"):
                video_probe.probe_and_validate(fake_video)
