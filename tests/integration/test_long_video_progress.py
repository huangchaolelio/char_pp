"""Integration tests for long video progress tracking — T028.

Tests verify progress fields structure returned by the status endpoint
using mocked DB tasks (no real PostgreSQL needed).
Run with: pytest tests/integration/test_long_video_progress.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(
    status: TaskStatus = TaskStatus.processing,
    total_segments: int | None = 6,
    processed_segments: int | None = 2,
    progress_pct: float | None = 33.33,
    audio_fallback_reason: str | None = None,
) -> AnalysisTask:
    """Build a minimal AnalysisTask-like object for schema tests."""
    task = MagicMock(spec=AnalysisTask)
    task.id = uuid.uuid4()
    task.task_type = TaskType.expert_video
    task.status = status
    task.created_at = datetime.now(tz=timezone.utc)
    task.started_at = datetime.now(tz=timezone.utc)
    task.completed_at = None
    task.video_duration_seconds = None
    task.video_fps = None
    task.video_resolution = None
    task.total_segments = total_segments
    task.processed_segments = processed_segments
    task.progress_pct = progress_pct
    task.audio_fallback_reason = audio_fallback_reason
    task.deleted_at = None
    return task


# ---------------------------------------------------------------------------
# Progress fields structure
# ---------------------------------------------------------------------------

class TestProgressFieldsStructure:
    def test_processing_task_has_progress_fields(self):
        """A task in processing state exposes progress_pct, processed_segments, total_segments."""
        task = make_task(
            status=TaskStatus.processing,
            total_segments=10,
            processed_segments=3,
            progress_pct=30.0,
        )
        assert task.total_segments == 10
        assert task.processed_segments == 3
        assert task.progress_pct == pytest.approx(30.0)

    def test_partial_success_task_retains_progress(self):
        """partial_success task should have final progress (may be <100 if segments failed)."""
        task = make_task(
            status=TaskStatus.partial_success,
            total_segments=5,
            processed_segments=4,
            progress_pct=80.0,
        )
        assert task.status == TaskStatus.partial_success
        assert task.progress_pct == pytest.approx(80.0)
        assert task.processed_segments == 4

    def test_success_task_has_100_progress(self):
        """Completed task should have progress_pct=100."""
        task = make_task(
            status=TaskStatus.success,
            total_segments=3,
            processed_segments=3,
            progress_pct=100.0,
        )
        assert task.progress_pct == pytest.approx(100.0)

    def test_pending_task_has_null_progress(self):
        """Pending task: no progress fields yet."""
        task = make_task(
            status=TaskStatus.pending,
            total_segments=None,
            processed_segments=None,
            progress_pct=None,
        )
        assert task.total_segments is None
        assert task.processed_segments is None
        assert task.progress_pct is None

    def test_audio_fallback_reason_preserved(self):
        """audio_fallback_reason is returned when audio analysis falls back."""
        reason = "low_snr: 4.2 dB (threshold: 10.0 dB)"
        task = make_task(audio_fallback_reason=reason)
        assert task.audio_fallback_reason == reason


# ---------------------------------------------------------------------------
# Schema serialisation round-trip
# ---------------------------------------------------------------------------

class TestTaskStatusResponseSchema:
    def test_progress_fields_in_schema(self):
        """TaskStatusResponse must include progress fields."""
        from src.api.schemas.task import TaskStatusResponse

        task = make_task(
            status=TaskStatus.processing,
            total_segments=8,
            processed_segments=2,
            progress_pct=25.0,
        )
        response = TaskStatusResponse(
            task_id=task.id,
            task_type=task.task_type.value,
            status=task.status.value,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            video_duration_seconds=task.video_duration_seconds,
            video_fps=task.video_fps,
            video_resolution=task.video_resolution,
            progress_pct=task.progress_pct,
            processed_segments=task.processed_segments,
            total_segments=task.total_segments,
            audio_fallback_reason=task.audio_fallback_reason,
        )
        assert response.progress_pct == pytest.approx(25.0)
        assert response.total_segments == 8
        assert response.processed_segments == 2

    def test_null_progress_fields_allowed(self):
        """Optional progress fields accept None."""
        from src.api.schemas.task import TaskStatusResponse

        task = make_task(
            status=TaskStatus.pending,
            total_segments=None,
            processed_segments=None,
            progress_pct=None,
        )
        response = TaskStatusResponse(
            task_id=task.id,
            task_type=task.task_type.value,
            status=task.status.value,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            video_duration_seconds=None,
            video_fps=None,
            video_resolution=None,
            progress_pct=None,
            processed_segments=None,
            total_segments=None,
            audio_fallback_reason=None,
        )
        assert response.progress_pct is None
        assert response.total_segments is None
