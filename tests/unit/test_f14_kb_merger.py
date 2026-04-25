"""Unit tests for Feature 014 KbMerger (T036).

F-014 semantics differ from Feature-002's ``src/services/kb_merger.py``:
- Conflicts are NOT written to the KB. They go to ``kb_conflicts`` for review.
- Non-conflicting items from BOTH paths are kept in the final KB list.
- Audio-only items with confidence < 0.5 are discarded (spec FR-009 intent).

The merger operates on plain dicts (no DB needed) so it's pure + unit-testable.
"""

from __future__ import annotations

import pytest

from src.services.kb_extraction_pipeline.merger import (
    F14KbMerger,
    MergedPoint,
    ConflictItem,
)


pytestmark = pytest.mark.unit


def _visual(dim: str, ideal: float, lo: float, hi: float, unit: str = "°",
            conf: float = 0.9, action_type: str = "forehand_topspin") -> dict:
    return {
        "dimension": dim,
        "param_min": lo,
        "param_max": hi,
        "param_ideal": ideal,
        "unit": unit,
        "extraction_confidence": conf,
        "action_type": action_type,
        "source_type": "visual",
    }


def _audio(dim: str, ideal: float, lo: float, hi: float, unit: str = "°",
           conf: float = 0.8, action_type: str = "forehand_topspin") -> dict:
    return {
        "dimension": dim,
        "param_min": lo,
        "param_max": hi,
        "param_ideal": ideal,
        "unit": unit,
        "extraction_confidence": conf,
        "action_type": action_type,
        "source_type": "audio",
    }


class TestMergeAlignedDimensions:
    """When visual + audio agree on a dimension → single merged entry, no conflict."""

    def test_two_aligned_points_produce_one_merged_no_conflict(self) -> None:
        merger = F14KbMerger()
        merged, conflicts = merger.merge(
            visual_items=[_visual("elbow_angle", 105, 90, 120)],
            audio_items=[_audio("elbow_angle", 106, 95, 118)],
        )
        assert len(conflicts) == 0
        assert len(merged) == 1
        assert merged[0].source_type == "visual+audio"
        assert merged[0].dimension == "elbow_angle"
        # param_ideal = average(105, 106) = 105.5
        assert merged[0].param_ideal == pytest.approx(105.5, abs=0.6)

    def test_diff_pct_exactly_at_threshold_is_not_conflict(self) -> None:
        # 10% diff boundary — default threshold is 0.10; at-threshold = merged.
        merger = F14KbMerger(conflict_threshold_pct=0.10)
        merged, conflicts = merger.merge(
            visual_items=[_visual("d", 100.0, 80, 120)],
            audio_items=[_audio("d", 110.0, 95, 125)],
        )
        assert len(conflicts) == 0
        assert len(merged) == 1


class TestMergeConflict:
    """When visual + audio disagree beyond threshold → conflict isolated, NOT in KB."""

    def test_large_diff_becomes_conflict_and_drops_from_kb(self) -> None:
        merger = F14KbMerger()
        merged, conflicts = merger.merge(
            visual_items=[_visual("elbow_angle", 105, 90, 120)],
            audio_items=[_audio("elbow_angle", 130, 125, 140)],
        )
        # Key F-014 rule: conflict items do NOT enter the merged list.
        assert len(merged) == 0
        assert len(conflicts) == 1
        c = conflicts[0]
        assert isinstance(c, ConflictItem)
        assert c.dimension_name == "elbow_angle"
        assert c.visual_value is not None
        assert c.audio_value is not None
        assert c.visual_value["ideal"] == 105
        assert c.audio_value["ideal"] == 130

    def test_conflict_records_confidence_from_both_sides(self) -> None:
        merger = F14KbMerger()
        _, conflicts = merger.merge(
            visual_items=[_visual("d", 100, 80, 120, conf=0.85)],
            audio_items=[_audio("d", 150, 140, 160, conf=0.75)],
        )
        c = conflicts[0]
        assert c.visual_confidence == 0.85
        assert c.audio_confidence == 0.75


class TestSingleSource:
    """Dimensions seen by only one path go straight through."""

    def test_visual_only_dimension_kept_with_visual_source(self) -> None:
        merger = F14KbMerger()
        merged, conflicts = merger.merge(
            visual_items=[_visual("wrist_arc", 1.2, 0.8, 1.5)],
            audio_items=[],
        )
        assert len(conflicts) == 0
        assert len(merged) == 1
        assert merged[0].source_type == "visual"
        assert merged[0].dimension == "wrist_arc"

    def test_audio_only_dimension_kept_with_audio_source(self) -> None:
        merger = F14KbMerger()
        merged, conflicts = merger.merge(
            visual_items=[],
            audio_items=[_audio("recovery_time", 320, 250, 380, unit="ms")],
        )
        assert len(conflicts) == 0
        assert len(merged) == 1
        assert merged[0].source_type == "audio"
        assert merged[0].unit == "ms"


class TestAudioConfidenceFilter:
    """Audio items with confidence < 0.5 are discarded before merge (FR-009)."""

    def test_low_confidence_audio_dropped_before_merge(self) -> None:
        merger = F14KbMerger()
        merged, conflicts = merger.merge(
            visual_items=[],
            audio_items=[_audio("wrist_arc", 1.5, 1.0, 2.0, conf=0.3)],
        )
        # The only audio item was below 0.5 confidence → dropped entirely.
        assert merged == []
        assert conflicts == []

    def test_low_conf_audio_does_not_override_visual(self) -> None:
        merger = F14KbMerger()
        merged, conflicts = merger.merge(
            visual_items=[_visual("d", 100, 80, 120)],
            audio_items=[_audio("d", 200, 180, 220, conf=0.3)],  # discarded
        )
        assert len(conflicts) == 0
        assert len(merged) == 1
        assert merged[0].source_type == "visual"  # audio ignored, stays visual-only
        assert merged[0].param_ideal == 100


class TestDegradationMode:
    """audio_items=[] (skipped/failed) still produces a valid visual-only KB."""

    def test_empty_audio_returns_visual_only_points(self) -> None:
        merger = F14KbMerger()
        merged, conflicts = merger.merge(
            visual_items=[
                _visual("elbow_angle", 105, 90, 120),
                _visual("wrist_arc", 1.1, 0.8, 1.4),
            ],
            audio_items=[],
        )
        assert len(conflicts) == 0
        assert len(merged) == 2
        assert all(m.source_type == "visual" for m in merged)


class TestMixedScenarios:
    def test_mix_of_aligned_conflict_and_single_source(self) -> None:
        merger = F14KbMerger()
        merged, conflicts = merger.merge(
            visual_items=[
                _visual("elbow_angle", 105, 90, 120),        # aligned w/ audio
                _visual("wrist_arc", 1.1, 0.8, 1.4),         # visual-only
                _visual("contact_timing", 200, 150, 250),    # conflict w/ audio
            ],
            audio_items=[
                _audio("elbow_angle", 108, 100, 118),        # aligned
                _audio("contact_timing", 350, 300, 400),     # conflict (75% diff)
                _audio("stance_width", 0.9, 0.7, 1.1, unit="ratio"),  # audio-only
            ],
        )
        # Expected:
        # - elbow_angle → merged (visual+audio)
        # - wrist_arc → visual-only
        # - contact_timing → conflict, NOT in merged
        # - stance_width → audio-only
        dims = {m.dimension: m for m in merged}
        assert "elbow_angle" in dims
        assert dims["elbow_angle"].source_type == "visual+audio"
        assert "wrist_arc" in dims
        assert dims["wrist_arc"].source_type == "visual"
        assert "stance_width" in dims
        assert dims["stance_width"].source_type == "audio"
        assert "contact_timing" not in dims
        # Only contact_timing is a conflict
        assert len(conflicts) == 1
        assert conflicts[0].dimension_name == "contact_timing"
