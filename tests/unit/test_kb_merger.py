"""Unit tests for KbMerger — T012.

Tests verify visual+audio merge logic, conflict detection,
source_type assignment, and pure visual/audio passthrough.
Run with: pytest tests/unit/test_kb_merger.py -v
"""

import pytest

from src.services.kb_merger import KbMerger, MergedTechPoint
from src.models.tech_semantic_segment import TechSemanticSegment


def make_visual_point(dimension: str, min_v: float, max_v: float, ideal_v: float, unit: str = "°") -> dict:
    """Helper to create a visual ExtractionResult-like dict."""
    return {
        "dimension": dimension,
        "param_min": min_v,
        "param_max": max_v,
        "param_ideal": ideal_v,
        "unit": unit,
        "extraction_confidence": 0.85,
        "action_type": "forehand_topspin",
    }


def make_audio_segment(dimension: str, min_v: float, max_v: float, ideal_v: float, unit: str = "°") -> TechSemanticSegment:
    """Helper to create a TechSemanticSegment for audio source."""
    seg = TechSemanticSegment()
    seg.dimension = dimension
    seg.param_min = min_v
    seg.param_max = max_v
    seg.param_ideal = ideal_v
    seg.unit = unit
    seg.parse_confidence = 0.82
    seg.is_reference_note = False
    return seg


@pytest.fixture
def merger():
    return KbMerger(conflict_threshold_pct=0.15)


class TestNoConflictMerge:
    def test_same_dimension_within_threshold_merges(self, merger):
        """Visual elbow_angle 90-120 + Audio 92-118 → diff < 15% → visual+audio, no conflict."""
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 105.0)]
        audio = [make_audio_segment("elbow_angle", 92.0, 118.0, 105.0)]
        results = merger.merge(visual, audio)
        merged = [r for r in results if r.dimension == "elbow_angle"]
        assert len(merged) == 1
        assert merged[0].source_type == "visual+audio"
        assert merged[0].conflict_flag is False

    def test_merged_ideal_is_average(self, merger):
        """Merged param_ideal should be average of visual and audio ideals."""
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 100.0)]
        audio = [make_audio_segment("elbow_angle", 90.0, 120.0, 110.0)]
        results = merger.merge(visual, audio)
        merged = [r for r in results if r.dimension == "elbow_angle"]
        assert merged[0].param_ideal == pytest.approx(105.0)


class TestConflictDetection:
    def test_large_diff_sets_conflict_flag(self, merger):
        """Visual 90-120 vs Audio 60-80 → diff > 15% → conflict_flag=True."""
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 105.0)]
        audio = [make_audio_segment("elbow_angle", 60.0, 80.0, 70.0)]
        results = merger.merge(visual, audio)
        conflicted = [r for r in results if r.dimension == "elbow_angle"]
        assert len(conflicted) == 1
        assert conflicted[0].conflict_flag is True
        assert conflicted[0].conflict_detail is not None
        assert "visual" in conflicted[0].conflict_detail
        assert "audio" in conflicted[0].conflict_detail

    def test_conflict_detail_contains_diff_pct(self, merger):
        """conflict_detail should include diff_pct key."""
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 105.0)]
        audio = [make_audio_segment("elbow_angle", 50.0, 70.0, 60.0)]
        results = merger.merge(visual, audio)
        conflicted = [r for r in results if r.conflict_flag]
        assert "diff_pct" in conflicted[0].conflict_detail


class TestSourceTypeAssignment:
    def test_visual_only_gets_visual_source(self, merger):
        """Point only in visual → source_type='visual'."""
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 105.0)]
        results = merger.merge(visual, [])
        assert results[0].source_type == "visual"
        assert results[0].conflict_flag is False

    def test_audio_only_gets_audio_source(self, merger):
        """Point only in audio → source_type='audio'."""
        audio = [make_audio_segment("wrist_angle", 30.0, 60.0, 45.0)]
        results = merger.merge([], audio)
        assert results[0].source_type == "audio"
        assert results[0].conflict_flag is False

    def test_multiple_dimensions_independent(self, merger):
        """Different dimensions from different sources all preserved independently."""
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 105.0)]
        audio = [make_audio_segment("wrist_angle", 30.0, 60.0, 45.0)]
        results = merger.merge(visual, audio)
        dims = {r.dimension for r in results}
        assert "elbow_angle" in dims
        assert "wrist_angle" in dims

    def test_reference_notes_excluded_from_merge(self, merger):
        """TechSemanticSegment with is_reference_note=True is not merged into KB."""
        ref_note = make_audio_segment("elbow_angle", 0, 0, 0)
        ref_note.is_reference_note = True
        ref_note.dimension = None
        results = merger.merge([], [ref_note])
        assert len(results) == 0


class TestEdgeCases:
    def test_empty_inputs_returns_empty(self, merger):
        assert merger.merge([], []) == []

    def test_exact_threshold_boundary_no_conflict(self, merger):
        """Diff exactly at threshold (15%) should NOT trigger conflict."""
        # visual ideal=100, audio ideal=115 → diff = 15% exactly
        visual = [make_visual_point("elbow_angle", 90.0, 110.0, 100.0)]
        audio = [make_audio_segment("elbow_angle", 105.0, 125.0, 115.0)]
        results = merger.merge(visual, audio)
        merged = [r for r in results if r.dimension == "elbow_angle"]
        assert merged[0].conflict_flag is False

    def test_just_over_threshold_triggers_conflict(self, merger):
        """Diff slightly above threshold (>15%) should trigger conflict."""
        visual = [make_visual_point("elbow_angle", 90.0, 110.0, 100.0)]
        audio = [make_audio_segment("elbow_angle", 106.0, 126.0, 116.0)]
        results = merger.merge(visual, audio)
        merged = [r for r in results if r.dimension == "elbow_angle"]
        assert merged[0].conflict_flag is True
