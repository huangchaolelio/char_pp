"""Unit tests — Feature 014 skipped-propagation semantics (T064).

Exercises ``Orchestrator._find_ready_steps`` across the key failure-position
scenarios so we lock down the DAG's behaviour without having to spin up a DB.

Covered scenarios:
  - download_video fails → no step is ever ready (entire DAG halted by live
    orchestrator via _propagate_skipped; _find_ready_steps just sees every
    downstream as skipped and stops picking them).
  - pose_analysis fails → visual + merge_kb cannot proceed; audio branch
    continues independently.
  - audio path fails → visual branch still progresses; merge_kb still ready
    in degradation mode (FR-012).
  - visual_kb_extract fails → merge_kb cannot degrade (visual is the hard
    requirement); merge_kb stays blocked.
  - merge_kb pending but visual still running → merge_kb not ready (strict
    barrier).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.models.pipeline_step import PipelineStepStatus, StepType
from src.services.kb_extraction_pipeline.orchestrator import Orchestrator


pytestmark = pytest.mark.unit


def _state_factory(overrides: dict[StepType, PipelineStepStatus]) -> dict[StepType, MagicMock]:
    """Build a ``{step_type: step}`` mapping where every step defaults to
    ``pending`` and ``overrides`` replaces specific rows."""
    states: dict[StepType, MagicMock] = {}
    for st in StepType:
        s = MagicMock()
        s.step_type = st
        s.status = overrides.get(st, PipelineStepStatus.pending)
        states[st] = s
    return states


class TestPropagateSemantics:
    def test_download_fail_all_downstream_skipped_nothing_ready(self) -> None:
        """When download is failed + everything downstream is skipped, no
        step is pickable (we expect _propagate_skipped to have been called
        by the live orchestrator before this snapshot)."""
        states = _state_factory(
            {
                StepType.download_video: PipelineStepStatus.failed,
                StepType.pose_analysis: PipelineStepStatus.skipped,
                StepType.audio_transcription: PipelineStepStatus.skipped,
                StepType.visual_kb_extract: PipelineStepStatus.skipped,
                StepType.audio_kb_extract: PipelineStepStatus.skipped,
                StepType.merge_kb: PipelineStepStatus.skipped,
            }
        )
        assert Orchestrator()._find_ready_steps(states) == []

    def test_pose_fail_visual_blocked_audio_progresses(self) -> None:
        """pose failed → visual_kb_extract skipped. audio path keeps going."""
        states = _state_factory(
            {
                StepType.download_video: PipelineStepStatus.success,
                StepType.pose_analysis: PipelineStepStatus.failed,
                StepType.audio_transcription: PipelineStepStatus.success,
                StepType.visual_kb_extract: PipelineStepStatus.skipped,
            }
        )
        ready = {s.step_type for s in Orchestrator()._find_ready_steps(states)}
        assert ready == {StepType.audio_kb_extract}

    def test_audio_transcription_fail_merge_still_degrades(self) -> None:
        """audio_transcription failed → audio_kb_extract skipped, but visual
        can still finish and merge_kb is ready in degradation mode."""
        states = _state_factory(
            {
                StepType.download_video: PipelineStepStatus.success,
                StepType.pose_analysis: PipelineStepStatus.success,
                StepType.audio_transcription: PipelineStepStatus.failed,
                StepType.visual_kb_extract: PipelineStepStatus.success,
                StepType.audio_kb_extract: PipelineStepStatus.skipped,
            }
        )
        ready = {s.step_type for s in Orchestrator()._find_ready_steps(states)}
        assert ready == {StepType.merge_kb}

    def test_visual_fail_merge_cannot_degrade(self) -> None:
        """visual_kb_extract failed → merge_kb is blocked (visual is the
        hard requirement; merge_kb cannot degrade to audio-only)."""
        states = _state_factory(
            {
                StepType.download_video: PipelineStepStatus.success,
                StepType.pose_analysis: PipelineStepStatus.success,
                StepType.audio_transcription: PipelineStepStatus.success,
                StepType.visual_kb_extract: PipelineStepStatus.failed,
                StepType.audio_kb_extract: PipelineStepStatus.success,
            }
        )
        ready = {s.step_type for s in Orchestrator()._find_ready_steps(states)}
        assert ready == set()

    def test_merge_blocked_while_visual_running(self) -> None:
        """Strict barrier: merge_kb NOT ready until visual has terminated
        (running means not terminal)."""
        states = _state_factory(
            {
                StepType.download_video: PipelineStepStatus.success,
                StepType.pose_analysis: PipelineStepStatus.success,
                StepType.audio_transcription: PipelineStepStatus.success,
                StepType.visual_kb_extract: PipelineStepStatus.running,
                StepType.audio_kb_extract: PipelineStepStatus.success,
            }
        )
        ready = {s.step_type for s in Orchestrator()._find_ready_steps(states)}
        assert ready == set()

    def test_happy_path_merge_ready_when_both_branches_success(self) -> None:
        states = _state_factory(
            {
                StepType.download_video: PipelineStepStatus.success,
                StepType.pose_analysis: PipelineStepStatus.success,
                StepType.audio_transcription: PipelineStepStatus.success,
                StepType.visual_kb_extract: PipelineStepStatus.success,
                StepType.audio_kb_extract: PipelineStepStatus.success,
            }
        )
        ready = {s.step_type for s in Orchestrator()._find_ready_steps(states)}
        assert ready == {StepType.merge_kb}
