"""Unit tests for COS client (T048).

Tests:
  - object_exists returns True/False correctly
  - download_to_temp success path
  - CosObjectNotFoundError raised when object missing
  - CosDownloadError raised on download failure
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.cos_client import (
    CosDownloadError,
    CosObjectNotFoundError,
    cleanup_temp_file,
    download_to_temp,
    object_exists,
)


@pytest.fixture(autouse=True)
def mock_settings():
    """Provide minimal settings for all tests."""
    settings = MagicMock()
    settings.cos_secret_id = "test_id"
    settings.cos_secret_key = "test_key"
    settings.cos_region = "ap-guangzhou"
    settings.cos_bucket = "test-bucket"
    settings.tmp_dir = Path("/tmp/test-coach")
    with patch("src.services.cos_client.get_settings", return_value=settings):
        yield settings


@pytest.mark.unit
class TestObjectExists:
    def test_returns_true_when_object_exists(self, mock_settings):
        mock_client = MagicMock()
        mock_client.head_object.return_value = {}
        with patch("src.services.cos_client._get_cos_client", return_value=(mock_client, "test-bucket")):
            result = object_exists("coach-videos/test.mp4")
        assert result is True

    def test_returns_false_when_object_missing(self, mock_settings):
        from qcloud_cos.cos_exception import CosServiceError

        mock_client = MagicMock()
        err = CosServiceError("HEAD", "NoSuchKey", 404)
        mock_client.head_object.side_effect = err
        with patch("src.services.cos_client._get_cos_client", return_value=(mock_client, "test-bucket")):
            result = object_exists("coach-videos/missing.mp4")
        assert result is False

    def test_returns_false_on_generic_exception(self, mock_settings):
        mock_client = MagicMock()
        mock_client.head_object.side_effect = Exception("network error")
        with patch("src.services.cos_client._get_cos_client", return_value=(mock_client, "test-bucket")):
            result = object_exists("coach-videos/test.mp4")
        assert result is False


@pytest.mark.unit
class TestDownloadToTemp:
    def test_download_success(self, mock_settings, tmp_path):
        mock_settings.tmp_dir = tmp_path

        mock_body = MagicMock()

        def fake_get_stream_to_file(dest_path):
            Path(dest_path).write_bytes(b"fake video content")

        mock_body.get_stream_to_file.side_effect = fake_get_stream_to_file
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": mock_body}

        with patch("src.services.cos_client._get_cos_client", return_value=(mock_client, "test-bucket")):
            result = download_to_temp("coach-videos/test.mp4")

        assert result.exists()
        assert result.read_bytes() == b"fake video content"

    def test_raises_cos_object_not_found(self, mock_settings, tmp_path):
        mock_settings.tmp_dir = tmp_path
        from qcloud_cos.cos_exception import CosServiceError

        mock_client = MagicMock()
        err = CosServiceError("GET", "NoSuchKey", 404)
        mock_client.get_object.side_effect = err

        with patch("src.services.cos_client._get_cos_client", return_value=(mock_client, "test-bucket")):
            with pytest.raises(CosObjectNotFoundError):
                download_to_temp("coach-videos/missing.mp4")

    def test_raises_cos_download_error_on_failure(self, mock_settings, tmp_path):
        mock_settings.tmp_dir = tmp_path
        from qcloud_cos.cos_exception import CosServiceError

        mock_client = MagicMock()
        err = CosServiceError("GET", "InternalError", 500)
        mock_client.get_object.side_effect = err

        with patch("src.services.cos_client._get_cos_client", return_value=(mock_client, "test-bucket")):
            with pytest.raises(CosDownloadError):
                download_to_temp("coach-videos/test.mp4")


@pytest.mark.unit
class TestCleanupTempFile:
    def test_cleanup_removes_file(self, tmp_path):
        tmp_file = tmp_path / "test.mp4"
        tmp_file.write_bytes(b"data")
        assert tmp_file.exists()
        cleanup_temp_file(tmp_file)
        assert not tmp_file.exists()

    def test_cleanup_ignores_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.mp4"
        # Should not raise
        cleanup_temp_file(missing)
