"""T025: Focused tests for temp file cleanup in DiagnosisService.finally block (NFR-003).

Verifies:
- Temp file deleted when tmp_path != input video_path (success path)
- Temp file NOT deleted when tmp_path == input video_path (local file, no download)
- Temp file deleted even when ExtractionFailedError is raised (exception path)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from src.utils.time_utils import now_cst
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.diagnosis_service import DiagnosisService, ExtractionFailedError


def _make_mock_standard():
    point = MagicMock()
    point.dimension = "elbow_angle"
    point.min = 85.0
    point.max = 105.0
    point.ideal = 95.0
    point.unit = "°"

    standard = MagicMock()
    standard.id = 1
    standard.version = 1
    standard.points = [point]
    return standard


def _make_session(standard=None):
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = standard
    session.execute.return_value = result
    session.add = MagicMock()
    session.flush = AsyncMock()

    def _add_side(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = uuid.uuid4()
        if not hasattr(obj, "created_at") or obj.created_at is None:
            obj.created_at = now_cst()

    session.add.side_effect = _add_side
    return session


@pytest.mark.asyncio
async def test_cleanup_called_when_tmp_differs_from_input():
    """finally deletes tmp_path when it differs from the original video_path."""
    standard = _make_mock_standard()
    session = _make_session(standard=standard)
    service = DiagnosisService(session)

    with (
        patch.object(service, "_localize_video", AsyncMock(return_value="/tmp/abc123.mp4")),
        patch.object(service, "_extract_measurements", AsyncMock(return_value={"elbow_angle": 90.0})),
        patch("src.services.diagnosis_service.LlmClient") as mock_llm_cls,
        patch("src.services.diagnosis_service.generate_improvement_advice", return_value=None),
        patch("src.services.diagnosis_service.os.unlink") as mock_unlink,
    ):
        mock_llm_cls.from_settings.return_value = MagicMock()
        await service.diagnose("forehand_topspin", "/original/video.mp4")

    mock_unlink.assert_called_once_with("/tmp/abc123.mp4")


@pytest.mark.asyncio
async def test_no_cleanup_when_tmp_equals_input():
    """finally does NOT delete when tmp_path == input (local file, no download)."""
    standard = _make_mock_standard()
    session = _make_session(standard=standard)
    service = DiagnosisService(session)

    # _localize_video returns the same path as input
    with (
        patch.object(service, "_localize_video", AsyncMock(return_value="/local/video.mp4")),
        patch.object(service, "_extract_measurements", AsyncMock(return_value={"elbow_angle": 90.0})),
        patch("src.services.diagnosis_service.LlmClient") as mock_llm_cls,
        patch("src.services.diagnosis_service.generate_improvement_advice", return_value=None),
        patch("src.services.diagnosis_service.os.unlink") as mock_unlink,
    ):
        mock_llm_cls.from_settings.return_value = MagicMock()
        await service.diagnose("forehand_topspin", "/local/video.mp4")

    mock_unlink.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_called_after_extraction_error():
    """finally deletes tmp_path even when ExtractionFailedError is raised mid-flow."""
    standard = _make_mock_standard()
    session = _make_session(standard=standard)
    service = DiagnosisService(session)

    with (
        patch.object(service, "_localize_video", AsyncMock(return_value="/tmp/xyz789.mp4")),
        patch.object(service, "_extract_measurements", AsyncMock(return_value={})),
        patch("src.services.diagnosis_service.LlmClient") as mock_llm_cls,
        patch("src.services.diagnosis_service.os.unlink") as mock_unlink,
    ):
        mock_llm_cls.from_settings.return_value = MagicMock()
        with pytest.raises(ExtractionFailedError):
            await service.diagnose("forehand_topspin", "/original/video.mp4")

    mock_unlink.assert_called_once_with("/tmp/xyz789.mp4")
