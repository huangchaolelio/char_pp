"""Unit tests for Orchestrator._finalize_job + _find_ready_steps + _propagate_skipped (T024).

These tests exercise the DAG state transition logic without hitting the DB,
using lightweight MagicMock objects that mimic PipelineStep / ExtractionJob.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.models.pipeline_step import PipelineStepStatus, StepType
from src.services.kb_extraction_pipeline.orchestrator import Orchestrator


pytestmark = pytest.mark.unit


def _make_step(step_type: StepType, status: PipelineStepStatus) -> MagicMock:
    s = MagicMock()
    s.step_type = step_type
    s.status = status
    return s


def _all_pending() -> dict[StepType, MagicMock]:
    return {t: _make_step(t, PipelineStepStatus.pending) for t in StepType}


class TestFindReadySteps:
    def test_initial_state_only_download_ready(self) -> None:
        steps = _all_pending()
        ready = Orchestrator()._find_ready_steps(steps)
        assert [s.step_type for s in ready] == [StepType.download_video]

    def test_after_download_success_pose_and_audio_ready(self) -> None:
        steps = _all_pending()
        steps[StepType.download_video].status = PipelineStepStatus.success
        ready = Orchestrator()._find_ready_steps(steps)
        assert set(s.step_type for s in ready) == {
            StepType.pose_analysis,
            StepType.audio_transcription,
        }

    def test_merge_kb_waits_until_both_paths_terminal(self) -> None:
        steps = _all_pending()
        for st in (
            StepType.download_video,
            StepType.pose_analysis,
            StepType.audio_transcription,
        ):
            steps[st].status = PipelineStepStatus.success
        # visual extracted; audio still running
        steps[StepType.visual_kb_extract].status = PipelineStepStatus.success
        steps[StepType.audio_kb_extract].status = PipelineStepStatus.running

        ready = Orchestrator()._find_ready_steps(steps)
        # merge_kb should NOT be ready yet
        assert StepType.merge_kb not in {s.step_type for s in ready}

    def test_merge_kb_degrades_when_audio_skipped(self) -> None:
        steps = _all_pending()
        for st in (
            StepType.download_video,
            StepType.pose_analysis,
            StepType.audio_transcription,
        ):
            steps[st].status = PipelineStepStatus.success
        steps[StepType.visual_kb_extract].status = PipelineStepStatus.success
        steps[StepType.audio_kb_extract].status = PipelineStepStatus.skipped

        ready = Orchestrator()._find_ready_steps(steps)
        assert StepType.merge_kb in {s.step_type for s in ready}

    def test_merge_kb_degrades_when_audio_failed(self) -> None:
        steps = _all_pending()
        for st in (
            StepType.download_video,
            StepType.pose_analysis,
            StepType.audio_transcription,
        ):
            steps[st].status = PipelineStepStatus.success
        steps[StepType.visual_kb_extract].status = PipelineStepStatus.success
        steps[StepType.audio_kb_extract].status = PipelineStepStatus.failed

        ready = Orchestrator()._find_ready_steps(steps)
        assert StepType.merge_kb in {s.step_type for s in ready}

    def test_merge_kb_blocked_when_visual_failed(self) -> None:
        steps = _all_pending()
        steps[StepType.download_video].status = PipelineStepStatus.success
        steps[StepType.pose_analysis].status = PipelineStepStatus.success
        steps[StepType.audio_transcription].status = PipelineStepStatus.success
        steps[StepType.visual_kb_extract].status = PipelineStepStatus.failed
        steps[StepType.audio_kb_extract].status = PipelineStepStatus.success

        ready = Orchestrator()._find_ready_steps(steps)
        # visual failing means merge_kb cannot degrade — it's the hard path
        assert StepType.merge_kb not in {s.step_type for s in ready}

    def test_running_steps_not_returned_as_ready(self) -> None:
        steps = _all_pending()
        steps[StepType.download_video].status = PipelineStepStatus.running
        ready = Orchestrator()._find_ready_steps(steps)
        assert ready == []


class TestFinalizeJobStatus:
    """_finalize_job derives the job terminal status from merge_kb's state."""

    # Those tests run against real DB; kept as integration in tests/integration.
    pass
