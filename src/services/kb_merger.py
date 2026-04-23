"""KbMerger — merges visual and audio tech points into a unified knowledge base set.

Merge rules:
- Same dimension from both sources, diff ≤ threshold → source_type="visual+audio", no conflict
- Same dimension from both sources, diff > threshold  → source_type="visual+audio", conflict_flag=True
- Dimension only in visual  → source_type="visual"
- Dimension only in audio   → source_type="audio"
- TechSemanticSegment with is_reference_note=True → excluded from output

Conflict diff metric: abs(visual_ideal - audio_ideal) / max(abs(visual_ideal), 1e-6)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from src.models.tech_semantic_segment import TechSemanticSegment

logger = logging.getLogger(__name__)

DEFAULT_CONFLICT_THRESHOLD_PCT = 0.15


@dataclass
class MergedTechPoint:
    """Intermediate result from KbMerger before persisting to ExpertTechPoint."""

    dimension: str
    param_min: float
    param_max: float
    param_ideal: float
    unit: str
    extraction_confidence: float
    source_type: str  # "visual" | "audio" | "visual+audio"
    conflict_flag: bool = False
    conflict_detail: Optional[dict] = None
    transcript_segment_id: Optional[uuid.UUID] = None
    # action_type inherited from visual point when available
    action_type: Optional[str] = None


def _diff_pct(visual_ideal: float, audio_ideal: float) -> float:
    """Compute relative difference relative to visual_ideal (the baseline).

    Formula: abs(visual_ideal - audio_ideal) / max(abs(visual_ideal), 1e-6)
    This matches the spec: conflict when visual param deviates >15% from audio.
    """
    return abs(visual_ideal - audio_ideal) / max(abs(visual_ideal), 1e-6)


class KbMerger:
    """Merges visual extraction results with audio-parsed tech segments."""

    def __init__(self, conflict_threshold_pct: float = DEFAULT_CONFLICT_THRESHOLD_PCT) -> None:
        self.conflict_threshold_pct = conflict_threshold_pct

    def merge(
        self,
        visual_points: list[dict],
        audio_segments: list[TechSemanticSegment],
    ) -> list[MergedTechPoint]:
        """Merge visual and audio tech points.

        Args:
            visual_points: List of ExtractionResult-like dicts from tech_extractor.
                           Keys: dimension, param_min, param_max, param_ideal, unit,
                                 extraction_confidence, action_type
            audio_segments: List of TechSemanticSegment objects from TranscriptTechParser.
                            Only segments with is_reference_note=False are used.

        Returns:
            List of MergedTechPoint ready to be persisted as ExpertTechPoint rows.
        """
        # Filter out reference notes
        audio_kb = [s for s in audio_segments if not s.is_reference_note and s.dimension is not None]

        # Index by dimension
        visual_by_dim: dict[str, dict] = {p["dimension"]: p for p in visual_points}
        audio_by_dim: dict[str, TechSemanticSegment] = {s.dimension: s for s in audio_kb}

        all_dimensions = set(visual_by_dim) | set(audio_by_dim)
        results: list[MergedTechPoint] = []

        for dim in all_dimensions:
            v = visual_by_dim.get(dim)
            a = audio_by_dim.get(dim)

            if v and a:
                merged = self._merge_both(v, a)
            elif v:
                merged = MergedTechPoint(
                    dimension=dim,
                    param_min=v["param_min"],
                    param_max=v["param_max"],
                    param_ideal=v["param_ideal"],
                    unit=v["unit"],
                    extraction_confidence=v["extraction_confidence"],
                    source_type="visual",
                    action_type=v.get("action_type"),
                )
            else:  # audio only
                merged = MergedTechPoint(
                    dimension=dim,
                    param_min=a.param_min,
                    param_max=a.param_max,
                    param_ideal=a.param_ideal,
                    unit=a.unit or "°",
                    extraction_confidence=a.parse_confidence,
                    source_type="audio",
                    transcript_segment_id=getattr(a, "id", None),
                )

            results.append(merged)

        logger.info(
            "KbMerger: %d visual + %d audio → %d merged (%d conflicts)",
            len(visual_points),
            len(audio_kb),
            len(results),
            sum(1 for r in results if r.conflict_flag),
        )
        return results

    def _merge_both(self, v: dict, a: TechSemanticSegment) -> MergedTechPoint:
        """Merge a visual and audio point for the same dimension."""
        v_ideal = v["param_ideal"]
        a_ideal = a.param_ideal or v_ideal

        diff = _diff_pct(v_ideal, a_ideal)
        conflict = diff > self.conflict_threshold_pct

        if conflict:
            conflict_detail = {
                "visual": {
                    "param_min": v["param_min"],
                    "param_max": v["param_max"],
                    "param_ideal": v_ideal,
                },
                "audio": {
                    "param_min": a.param_min,
                    "param_max": a.param_max,
                    "param_ideal": a_ideal,
                },
                "diff_pct": round(diff, 4),
            }
            logger.warning(
                "Conflict detected for dimension '%s': visual_ideal=%.1f audio_ideal=%.1f diff=%.1f%%",
                v["dimension"], v_ideal, a_ideal, diff * 100,
            )
        else:
            conflict_detail = None

        return MergedTechPoint(
            dimension=v["dimension"],
            param_min=min(v["param_min"], a.param_min or v["param_min"]),
            param_max=max(v["param_max"], a.param_max or v["param_max"]),
            param_ideal=round((v_ideal + a_ideal) / 2, 2),
            unit=v["unit"],
            extraction_confidence=max(v["extraction_confidence"], a.parse_confidence),
            source_type="visual+audio",
            conflict_flag=conflict,
            conflict_detail=conflict_detail,
            transcript_segment_id=getattr(a, "id", None),
            action_type=v.get("action_type"),
        )
