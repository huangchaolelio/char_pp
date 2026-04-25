"""Unit tests for Feature-016 preprocessing_service.

Covers:
- create_or_reuse(force=False) with an existing success row → returns that
  row and marks reused=True. No new row is inserted.
- create_or_reuse(force=True) with an existing success row →
  (a) old row transitions to status='superseded',
  (b) old COS prefix is deleted,
  (c) a brand new running row is inserted,
  (d) reused=False.
- create_or_reuse raises CosKeyNotClassifiedError if the cos_object_key is
  not present in coach_video_classifications.
- mark_preprocessed(cos_object_key) sets preprocessed=True on the matching
  CoachVideoClassification row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


class _StubCoachRow:
    def __init__(self, cos_key: str):
        self.cos_object_key = cos_key
        self.preprocessed = False


class _StubJobRow:
    def __init__(self, cos_key: str, status: str = "running", force: bool = False):
        self.id = uuid4()
        self.cos_object_key = cos_key
        self.status = status
        self.force = force
        self.error_message = None
        self.started_at = datetime.now(timezone.utc)
        self.completed_at = datetime.now(timezone.utc) if status == "success" else None
        self.duration_ms = 600_000 if status == "success" else None
        self.segment_count = 4 if status == "success" else None
        self.has_audio = True
        self.audio_cos_object_key = "preproc/audio.wav" if status == "success" else None
        self.audio_size_bytes = 19_200_000 if status == "success" else None
        self.local_artifact_dir = None
        self.original_meta_json = None
        self.target_standard_json = None


@pytest.fixture
def fake_session():
    """A minimal AsyncSession-like stub we can spy on."""
    s = MagicMock(spec=AsyncSession)
    s.execute = AsyncMock()
    s.add = MagicMock()
    s.flush = AsyncMock()
    s.commit = AsyncMock()
    s.refresh = AsyncMock()
    return s


@pytest.mark.unit
class TestCreateOrReuse:
    @pytest.mark.asyncio
    async def test_force_false_reuses_existing_success(self, fake_session):
        from src.services import preprocessing_service

        coach = _StubCoachRow("coach/v.mp4")
        existing = _StubJobRow("coach/v.mp4", status="success")

        # ``create_or_reuse`` is expected to:
        # 1. check the video is classified
        # 2. look for an existing success job
        # 3. short-circuit → reused=True
        with patch.object(
            preprocessing_service, "_fetch_classification",
            AsyncMock(return_value=coach),
        ), patch.object(
            preprocessing_service, "_fetch_success_job",
            AsyncMock(return_value=existing),
        ) as mock_fetch_success, patch.object(
            preprocessing_service, "_fetch_channel_slot_available",
            AsyncMock(return_value=True),
        ):
            out = await preprocessing_service.create_or_reuse(
                fake_session, cos_object_key="coach/v.mp4", force=False,
            )

        assert out.reused is True
        assert out.status == "success"
        assert out.job_id == existing.id
        # No INSERT should happen.
        fake_session.add.assert_not_called()
        mock_fetch_success.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_force_true_supersedes_old_and_deletes_cos(self, fake_session):
        from src.services import preprocessing_service

        coach = _StubCoachRow("coach/v.mp4")
        old = _StubJobRow("coach/v.mp4", status="success")

        with patch.object(
            preprocessing_service, "_fetch_classification",
            AsyncMock(return_value=coach),
        ), patch.object(
            preprocessing_service, "_fetch_success_job",
            AsyncMock(return_value=old),
        ), patch.object(
            preprocessing_service, "_fetch_channel_slot_available",
            AsyncMock(return_value=True),
        ), patch(
            "src.services.preprocessing_service.cos_uploader.delete_prefix",
            return_value=3,
        ) as mock_delete, patch(
            "src.services.preprocessing_service._cos_prefix_for_job",
            return_value="preproc/coach/v.mp4/jobs/old/",
        ):
            out = await preprocessing_service.create_or_reuse(
                fake_session, cos_object_key="coach/v.mp4", force=True,
            )

        assert out.reused is False
        assert out.status == "running"
        # Old row got superseded
        assert old.status == "superseded"
        # New row was inserted
        fake_session.add.assert_called()
        # Old COS prefix got purged
        mock_delete.assert_called_once_with("preproc/coach/v.mp4/jobs/old/")

    @pytest.mark.asyncio
    async def test_unknown_cos_key_raises_not_classified(self, fake_session):
        from src.services import preprocessing_service

        with patch.object(
            preprocessing_service, "_fetch_classification",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(preprocessing_service.CosKeyNotClassifiedError):
                await preprocessing_service.create_or_reuse(
                    fake_session,
                    cos_object_key="does/not/exist.mp4",
                    force=False,
                )

    @pytest.mark.asyncio
    async def test_channel_full_raises_queue_full(self, fake_session):
        from src.services import preprocessing_service

        coach = _StubCoachRow("coach/v.mp4")
        with patch.object(
            preprocessing_service, "_fetch_classification",
            AsyncMock(return_value=coach),
        ), patch.object(
            preprocessing_service, "_fetch_success_job",
            AsyncMock(return_value=None),
        ), patch.object(
            preprocessing_service, "_fetch_channel_slot_available",
            AsyncMock(return_value=False),
        ):
            with pytest.raises(preprocessing_service.ChannelQueueFullError):
                await preprocessing_service.create_or_reuse(
                    fake_session,
                    cos_object_key="coach/v.mp4",
                    force=False,
                )


@pytest.mark.unit
class TestMarkPreprocessed:
    @pytest.mark.asyncio
    async def test_flips_classification_row_true(self, fake_session):
        from src.services import preprocessing_service

        coach = _StubCoachRow("coach/v.mp4")
        with patch.object(
            preprocessing_service, "_fetch_classification",
            AsyncMock(return_value=coach),
        ):
            await preprocessing_service.mark_preprocessed(
                fake_session, cos_object_key="coach/v.mp4",
            )

        assert coach.preprocessed is True
