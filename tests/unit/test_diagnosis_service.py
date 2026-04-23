"""T015: Unit tests for DiagnosisService (mock all external dependencies).

T027: COS key parsing tests for _download_from_cos.

Tests use AsyncMock and patch to isolate DiagnosisService from:
- DB (AsyncSession)
- pose_estimator / tech_extractor pipeline
- LLM advisor
- COS client
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.diagnosis_service import (
    DiagnosisReportData,
    DiagnosisService,
    ExtractionFailedError,
    StandardNotFoundError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_standard(dimension="elbow_angle", min_=85.0, max_=105.0, ideal=95.0, unit="°"):
    """Build a mock TechStandard with one point."""
    point = MagicMock()
    point.dimension = dimension
    point.min = min_
    point.max = max_
    point.ideal = ideal
    point.unit = unit

    standard = MagicMock()
    standard.id = 1
    standard.version = 2
    standard.points = [point]
    return standard


def _make_session(standard=None):
    """Build a minimal mock AsyncSession."""
    session = AsyncMock()
    # execute returns a result whose scalar_one_or_none returns the standard
    result = MagicMock()
    result.scalar_one_or_none.return_value = standard
    session.execute.return_value = result
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()

    # After flush, report gets an id
    def _side_effect_add(obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = uuid.uuid4()
        if not hasattr(obj, "created_at") or obj.created_at is None:
            obj.created_at = datetime.now(timezone.utc)

    session.add.side_effect = _side_effect_add
    return session


# ---------------------------------------------------------------------------
# T015a: StandardNotFoundError propagated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_active_standard_raises_error():
    session = _make_session(standard=None)
    service = DiagnosisService(session)

    with pytest.raises(StandardNotFoundError):
        await service.diagnose("forehand_topspin", "/tmp/video.mp4")


# ---------------------------------------------------------------------------
# T015b: ExtractionFailedError on empty measurements
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_measurements_raises_extraction_failed():
    standard = _make_mock_standard()
    session = _make_session(standard=standard)
    service = DiagnosisService(session)

    with (
        patch.object(service, "_localize_video", AsyncMock(return_value="/tmp/fake.mp4")),
        patch.object(service, "_extract_measurements", AsyncMock(return_value={})),
        patch("src.services.diagnosis_service.LlmClient") as mock_llm_cls,
    ):
        mock_llm_cls.from_settings.return_value = MagicMock()
        with pytest.raises(ExtractionFailedError):
            await service.diagnose("forehand_topspin", "/tmp/video.mp4")


# ---------------------------------------------------------------------------
# T015c: Normal flow returns DiagnosisReportData
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_flow_returns_report_data():
    standard = _make_mock_standard()
    session = _make_session(standard=standard)
    service = DiagnosisService(session)

    with (
        patch.object(service, "_localize_video", AsyncMock(return_value="/tmp/fake.mp4")),
        patch.object(service, "_extract_measurements", AsyncMock(return_value={"elbow_angle": 90.0})),
        patch("src.services.diagnosis_service.LlmClient") as mock_llm_cls,
        patch("src.services.diagnosis_service.generate_improvement_advice", return_value=None),
        patch("src.services.diagnosis_service.os.unlink"),
    ):
        mock_llm_cls.from_settings.return_value = MagicMock()
        result = await service.diagnose("forehand_topspin", "/tmp/video.mp4")

    assert isinstance(result, DiagnosisReportData)
    assert result.tech_category == "forehand_topspin"
    assert 0.0 <= result.overall_score <= 100.0
    assert len(result.dimensions) == 1
    assert result.dimensions[0].dimension == "elbow_angle"


# ---------------------------------------------------------------------------
# T015d: LLM called via run_in_executor for deviant dimension
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_called_via_run_in_executor():
    """Deviant dimension (measured far out of range) triggers LLM advice call."""
    # measured=200.0 is far above max=105.0 → significant deviation
    standard = _make_mock_standard(dimension="elbow_angle", min_=85.0, max_=105.0, ideal=95.0)
    session = _make_session(standard=standard)
    service = DiagnosisService(session)

    with (
        patch.object(service, "_localize_video", AsyncMock(return_value="/tmp/fake.mp4")),
        patch.object(service, "_extract_measurements", AsyncMock(return_value={"elbow_angle": 200.0})),
        patch("src.services.diagnosis_service.LlmClient") as mock_llm_cls,
        patch("src.services.diagnosis_service.generate_improvement_advice", return_value="建议调整肘部角度") as mock_advice,
        patch("src.services.diagnosis_service.os.unlink"),
    ):
        mock_llm_cls.from_settings.return_value = MagicMock()
        result = await service.diagnose("forehand_topspin", "/tmp/video.mp4")

    # LLM advice function should have been called for the deviant dimension
    mock_advice.assert_called_once()
    assert result.dimensions[0].improvement_advice == "建议调整肘部角度"


# ---------------------------------------------------------------------------
# T015e / T025a: Temp file cleaned up on success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tmp_file_cleaned_up_on_success():
    """finally block deletes temp file when tmp_path differs from input path."""
    standard = _make_mock_standard()
    session = _make_session(standard=standard)
    service = DiagnosisService(session)

    with (
        patch.object(service, "_localize_video", AsyncMock(return_value="/tmp/test_cleanup_abc.mp4")),
        patch.object(service, "_extract_measurements", AsyncMock(return_value={"elbow_angle": 90.0})),
        patch("src.services.diagnosis_service.LlmClient") as mock_llm_cls,
        patch("src.services.diagnosis_service.generate_improvement_advice", return_value=None),
        patch("src.services.diagnosis_service.os.unlink") as mock_unlink,
    ):
        mock_llm_cls.from_settings.return_value = MagicMock()
        # Input path differs from tmp_path returned by _localize_video
        await service.diagnose("forehand_topspin", "/original/video.mp4")

    mock_unlink.assert_called_once_with("/tmp/test_cleanup_abc.mp4")


# ---------------------------------------------------------------------------
# T015f / T025c: Temp file cleaned up even after exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tmp_file_cleaned_up_on_exception():
    """finally block deletes temp file even when ExtractionFailedError is raised."""
    standard = _make_mock_standard()
    session = _make_session(standard=standard)
    service = DiagnosisService(session)

    with (
        patch.object(service, "_localize_video", AsyncMock(return_value="/tmp/test_err_cleanup.mp4")),
        patch.object(service, "_extract_measurements", AsyncMock(return_value={})),
        patch("src.services.diagnosis_service.LlmClient") as mock_llm_cls,
        patch("src.services.diagnosis_service.os.unlink") as mock_unlink,
    ):
        mock_llm_cls.from_settings.return_value = MagicMock()
        with pytest.raises(ExtractionFailedError):
            await service.diagnose("forehand_topspin", "/original/video.mp4")

    mock_unlink.assert_called_once_with("/tmp/test_err_cleanup.mp4")


# ---------------------------------------------------------------------------
# T027: COS key parsing tests
# ---------------------------------------------------------------------------

def _make_executor_mock(return_value: str) -> tuple[MagicMock, dict]:
    """Return (mock_loop, captured) where run_in_executor captures call args."""
    captured: dict = {}

    def fake_run_in_executor(executor, func, *args):
        captured["args"] = args
        # Return a coroutine that resolves to return_value
        async def _coro():
            return return_value
        return asyncio.ensure_future(_coro())

    mock_loop = MagicMock()
    mock_loop.run_in_executor.side_effect = fake_run_in_executor
    return mock_loop, captured


@pytest.mark.asyncio
async def test_cos_key_parsing_full_path():
    """cos://bucket-name/path/to/video.mp4 → key = path/to/video.mp4"""
    session = AsyncMock()
    service = DiagnosisService(session)
    mock_loop, captured = _make_executor_mock("/tmp/downloaded.mp4")

    with (
        patch("src.services.cos_client.download_to_temp"),
        patch("src.services.diagnosis_service.asyncio") as mock_asyncio,
    ):
        mock_asyncio.get_event_loop.return_value = mock_loop
        result = await service._download_from_cos("cos://bucket-name/path/to/video.mp4")

    assert captured["args"] == ("path/to/video.mp4",)
    assert result == "/tmp/downloaded.mp4"


@pytest.mark.asyncio
async def test_cos_key_parsing_simple():
    """cos://bucket/key.mp4 → key = key.mp4"""
    session = AsyncMock()
    service = DiagnosisService(session)
    mock_loop, captured = _make_executor_mock("/tmp/downloaded2.mp4")

    with (
        patch("src.services.cos_client.download_to_temp"),
        patch("src.services.diagnosis_service.asyncio") as mock_asyncio,
    ):
        mock_asyncio.get_event_loop.return_value = mock_loop
        result = await service._download_from_cos("cos://bucket/key.mp4")

    assert captured["args"] == ("key.mp4",)
    assert result == "/tmp/downloaded2.mp4"
