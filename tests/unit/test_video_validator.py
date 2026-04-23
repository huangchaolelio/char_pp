"""Unit tests for video quality validator (T049).

Tests:
  - fps below threshold → VideoQualityRejected("fps_too_low")
  - resolution below threshold → VideoQualityRejected("resolution_too_low")
  - unreadable video → VideoQualityRejected("unreadable")
  - valid video → returns VideoMeta
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.video_validator import VideoMeta, VideoQualityRejected, validate_video


def _make_cv2_capture(fps: float, width: int, height: int, frame_count: int = 300, is_opened: bool = True):
    """Helper to build a mock cv2.VideoCapture."""
    cap = MagicMock()
    cap.isOpened.return_value = is_opened
    cap.get.side_effect = lambda prop: {
        5: fps,          # CAP_PROP_FPS
        3: float(width), # CAP_PROP_FRAME_WIDTH
        4: float(height),# CAP_PROP_FRAME_HEIGHT
        7: float(frame_count),  # CAP_PROP_FRAME_COUNT
    }.get(prop, 0.0)
    return cap


@pytest.mark.unit
class TestValidateVideo:
    @patch("src.services.video_validator.cv2.VideoCapture")
    def test_valid_video_returns_meta(self, mock_vc):
        cap = _make_cv2_capture(fps=30.0, width=1920, height=1080, frame_count=450)
        mock_vc.return_value = cap

        meta = validate_video(Path("test.mp4"))
        assert isinstance(meta, VideoMeta)
        assert meta.fps == 30.0
        assert meta.resolution_str == "1920x1080"
        assert meta.duration_seconds > 0

    @patch("src.services.video_validator.cv2.VideoCapture")
    def test_fps_too_low_raises(self, mock_vc):
        cap = _make_cv2_capture(fps=10.0, width=1920, height=1080)
        mock_vc.return_value = cap

        with pytest.raises(VideoQualityRejected) as exc_info:
            validate_video(Path("test.mp4"))
        assert exc_info.value.reason == "fps_too_low"

    @patch("src.services.video_validator.cv2.VideoCapture")
    def test_resolution_too_low_raises(self, mock_vc):
        cap = _make_cv2_capture(fps=30.0, width=640, height=360)
        mock_vc.return_value = cap

        with pytest.raises(VideoQualityRejected) as exc_info:
            validate_video(Path("test.mp4"))
        assert exc_info.value.reason == "resolution_too_low"

    @patch("src.services.video_validator.cv2.VideoCapture")
    def test_unreadable_video_raises(self, mock_vc):
        cap = _make_cv2_capture(fps=0, width=0, height=0, is_opened=False)
        mock_vc.return_value = cap

        with pytest.raises(VideoQualityRejected) as exc_info:
            validate_video(Path("test.mp4"))
        assert exc_info.value.reason == "unreadable"

    @patch("src.services.video_validator.cv2.VideoCapture")
    def test_minimum_valid_fps_passes(self, mock_vc):
        """Exactly 15fps should pass."""
        cap = _make_cv2_capture(fps=15.0, width=854, height=480)
        mock_vc.return_value = cap

        meta = validate_video(Path("test.mp4"))
        assert meta.fps == 15.0

    @patch("src.services.video_validator.cv2.VideoCapture")
    def test_minimum_valid_resolution_passes(self, mock_vc):
        """Exactly 854x480 should pass."""
        cap = _make_cv2_capture(fps=30.0, width=854, height=480)
        mock_vc.return_value = cap

        meta = validate_video(Path("test.mp4"))
        assert meta.resolution_str == "854x480"
