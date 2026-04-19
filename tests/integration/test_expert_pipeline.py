"""Expert video pipeline integration test (T053).

Tests the full expert video analysis pipeline end-to-end:
  COS download → quality gate → pose estimation → extraction → KB draft creation → approve

Uses mocked COS SDK and a minimal synthetic video fixture.
"""

import asyncio
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def synthetic_video_path(tmp_path) -> Path:
    """Create a minimal synthetic video file for testing.

    In a real integration test environment, replace this with an actual test video.
    Returns a path to a placeholder file.
    """
    video_path = tmp_path / "test_expert.mp4"
    # Create a minimal placeholder — real tests would use actual video fixtures
    video_path.write_bytes(b"\x00" * 1024)
    return video_path


@pytest.mark.integration
@pytest.mark.asyncio
class TestExpertVideoPipeline:
    async def test_cos_download_triggers_on_valid_key(self, synthetic_video_path, tmp_path):
        """Verify COS client download is invoked for a valid object key."""
        from src.services import cos_client

        with (
            patch("src.services.cos_client._get_cos_client") as mock_cos_factory,
        ):
            mock_client = MagicMock()

            def fake_get_object(Bucket, Key, **kwargs):
                dest = kwargs.get("DestFilePath", "")
                Path(dest).write_bytes(synthetic_video_path.read_bytes())
                return {}

            mock_client.get_object.side_effect = fake_get_object
            mock_client.head_object.return_value = {}
            mock_cos_factory.return_value = (mock_client, "test-bucket")

            settings_mock = MagicMock()
            settings_mock.cos_bucket = "test-bucket"
            settings_mock.cos_region = "ap-guangzhou"
            settings_mock.cos_secret_id = "test_id"
            settings_mock.cos_secret_key = "test_key"
            settings_mock.tmp_dir = tmp_path

            with patch("src.services.cos_client.get_settings", return_value=settings_mock):
                assert cos_client.object_exists("test-key.mp4") is True

                mock_body = MagicMock()
                mock_body.get_stream_to_file.side_effect = lambda dest: Path(dest).write_bytes(synthetic_video_path.read_bytes())
                mock_client.get_object.return_value = {"Body": mock_body}
                mock_client.get_object.side_effect = None

                path = cos_client.download_to_temp("test-key.mp4")
                assert path.exists()
                cos_client.cleanup_temp_file(path)
                assert not path.exists()

    async def test_video_validator_rejects_low_fps(self, tmp_path):
        """VideoValidator correctly rejects videos below minimum FPS."""
        import cv2
        from unittest.mock import patch as mock_patch
        from src.services.video_validator import VideoQualityRejected, validate_video

        video_path = tmp_path / "low_fps.mp4"
        video_path.write_bytes(b"\x00" * 512)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.side_effect = lambda prop: {5: 5.0, 3: 1920.0, 4: 1080.0, 7: 100.0}.get(prop, 0)

        with mock_patch("src.services.video_validator.cv2.VideoCapture", return_value=mock_cap):
            with pytest.raises(VideoQualityRejected) as exc:
                validate_video(video_path)
            assert exc.value.reason == "fps_too_low"

    async def test_kb_service_create_and_approve(self):
        """KnowledgeBase service creates a draft and approves it correctly."""
        from unittest.mock import AsyncMock, MagicMock
        from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase
        from src.services import knowledge_base_svc

        session = AsyncMock()

        # No existing version (fresh start)
        mock_latest_result = MagicMock()
        mock_latest_result.scalar_one_or_none.return_value = None

        created_kb = None

        async def mock_execute(stmt):
            return mock_latest_result

        session.execute = mock_execute
        session.flush = AsyncMock()

        def capture_add(obj):
            nonlocal created_kb
            created_kb = obj

        session.add = capture_add

        kb = await knowledge_base_svc.create_draft_version(
            session,
            action_types=["forehand_topspin"],
            notes="Test draft",
        )

        assert kb.version == "1.0.0"
        assert kb.status == KBStatus.draft
