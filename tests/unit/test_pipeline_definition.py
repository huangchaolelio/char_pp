"""Unit tests for Feature 014 DAG definition (T023)."""

from __future__ import annotations

import pytest

from src.models.pipeline_step import StepType
from src.services.kb_extraction_pipeline.pipeline_definition import (
    CPU_STEPS,
    DEPENDENCIES,
    IO_STEPS,
    TOPOLOGICAL_ORDER,
    all_step_types,
    dependents_of,
)


pytestmark = pytest.mark.unit


class TestPipelineDefinition:
    def test_all_six_steps_defined(self) -> None:
        assert set(DEPENDENCIES.keys()) == set(StepType)
        assert len(DEPENDENCIES) == 6

    def test_download_video_has_no_deps(self) -> None:
        assert DEPENDENCIES[StepType.download_video] == []

    def test_merge_kb_depends_on_both_paths(self) -> None:
        deps = set(DEPENDENCIES[StepType.merge_kb])
        assert deps == {StepType.visual_kb_extract, StepType.audio_kb_extract}

    def test_visual_and_audio_paths_do_not_cross(self) -> None:
        # pose_analysis feeds only visual_kb_extract (not audio).
        assert DEPENDENCIES[StepType.visual_kb_extract] == [StepType.pose_analysis]
        # audio_transcription feeds only audio_kb_extract (not visual).
        assert DEPENDENCIES[StepType.audio_kb_extract] == [StepType.audio_transcription]

    def test_topological_order_respects_deps(self) -> None:
        order = TOPOLOGICAL_ORDER
        assert len(order) == 6
        assert set(order) == set(StepType)
        # Every step must appear after all its deps.
        index = {s: i for i, s in enumerate(order)}
        for step, deps in DEPENDENCIES.items():
            for d in deps:
                assert index[d] < index[step], (
                    f"{d.value} must come before {step.value} in topo order"
                )

    def test_io_and_cpu_partition_is_complete(self) -> None:
        assert IO_STEPS | CPU_STEPS == set(StepType)
        assert not (IO_STEPS & CPU_STEPS)

    def test_io_steps_content(self) -> None:
        assert IO_STEPS == {
            StepType.download_video,
            StepType.audio_transcription,
            StepType.audio_kb_extract,
        }

    def test_cpu_steps_content(self) -> None:
        assert CPU_STEPS == {
            StepType.pose_analysis,
            StepType.visual_kb_extract,
            StepType.merge_kb,
        }

    def test_dependents_of_download(self) -> None:
        deps = dependents_of(StepType.download_video)
        assert set(deps) == {StepType.pose_analysis, StepType.audio_transcription}

    def test_dependents_of_merge_kb_is_empty(self) -> None:
        assert dependents_of(StepType.merge_kb) == []

    def test_all_step_types_matches_topological_order(self) -> None:
        assert all_step_types() == list(TOPOLOGICAL_ORDER)
