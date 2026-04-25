"""Feature 014 KbMerger — conflict-aware merger of visual + audio KB items.

Differs from Feature-002's ``src/services/kb_merger.py``:

  * F-002 marks conflicts with ``conflict_flag=True`` but still writes them to
    the knowledge base.
  * F-014 spec (FR-011) requires conflicts to be *isolated* into
    ``kb_conflicts`` and **not** written to the main KB. Non-conflicting
    items from both paths flow through normally.

The merger is pure — no DB, no I/O — so it's fully unit-testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional


logger = logging.getLogger(__name__)


# Default conflict threshold (10%): tighter than Feature-002's 15% because
# F-014 routes conflicts to manual review — a lower bar surfaces more items
# for humans to adjudicate, matching the spec's spirit.
DEFAULT_CONFLICT_THRESHOLD_PCT = 0.10

# Audio items below this confidence are discarded before merge (FR-009 intent).
AUDIO_CONFIDENCE_DROP_THRESHOLD = 0.5


@dataclass
class MergedPoint:
    """A point that should end up in the main knowledge base (ExpertTechPoint)."""

    dimension: str
    param_min: float
    param_max: float
    param_ideal: float
    unit: str
    extraction_confidence: float
    source_type: str                 # "visual" | "audio" | "visual+audio"
    action_type: Optional[str] = None


@dataclass
class ConflictItem:
    """A dimension where visual and audio disagreed beyond threshold."""

    dimension_name: str
    visual_value: Optional[dict] = None       # {"min","max","ideal","unit"}
    audio_value: Optional[dict] = None        # {"min","max","ideal","unit"}
    visual_confidence: Optional[float] = None
    audio_confidence: Optional[float] = None
    diff_pct: float = 0.0
    action_type: Optional[str] = None
    tech_category: Optional[str] = None


class F14KbMerger:
    """Merge visual + audio KB item lists; isolate conflicts."""

    def __init__(
        self,
        conflict_threshold_pct: float = DEFAULT_CONFLICT_THRESHOLD_PCT,
        audio_min_confidence: float = AUDIO_CONFIDENCE_DROP_THRESHOLD,
    ) -> None:
        self.conflict_threshold_pct = conflict_threshold_pct
        self.audio_min_confidence = audio_min_confidence

    def merge(
        self,
        visual_items: list[dict],
        audio_items: list[dict],
    ) -> tuple[list[MergedPoint], list[ConflictItem]]:
        """Merge visual and audio KB item dicts.

        Input dict shape (both visual and audio):
          ``{"dimension", "param_min", "param_max", "param_ideal", "unit",
             "extraction_confidence", "action_type"}``

        Returns ``(merged_for_kb, conflicts)``:
          - ``merged_for_kb``: points to INSERT into ``expert_tech_points``.
          - ``conflicts``: records to INSERT into ``kb_conflicts`` (kept out of KB).
        """
        # 1) Filter: drop low-confidence audio entries so we don't let weak
        #    LLM outputs pollute the merge.
        audio_clean = [
            a for a in audio_items
            if float(a.get("extraction_confidence", 0.0)) >= self.audio_min_confidence
        ]

        # 2) Index both sides by dimension. If a side provides multiple points
        #    with the same dimension (shouldn't happen in practice), the last
        #    one wins — matching the equivalent behaviour of Feature-002.
        visual_by_dim = {v["dimension"]: v for v in visual_items}
        audio_by_dim = {a["dimension"]: a for a in audio_clean}

        merged: list[MergedPoint] = []
        conflicts: list[ConflictItem] = []

        for dim in sorted(set(visual_by_dim) | set(audio_by_dim)):
            v = visual_by_dim.get(dim)
            a = audio_by_dim.get(dim)

            if v and a:
                resolved = self._merge_both(dim, v, a)
                if isinstance(resolved, ConflictItem):
                    conflicts.append(resolved)
                else:
                    merged.append(resolved)
            elif v:
                merged.append(self._as_merged(v, "visual"))
            else:  # audio-only
                merged.append(self._as_merged(a, "audio"))  # type: ignore[arg-type]

        logger.info(
            "F14KbMerger: visual=%d audio_clean=%d → merged=%d conflicts=%d",
            len(visual_items), len(audio_clean), len(merged), len(conflicts),
        )
        return merged, conflicts

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _as_merged(item: dict, source_type: str) -> MergedPoint:
        return MergedPoint(
            dimension=item["dimension"],
            param_min=float(item["param_min"]),
            param_max=float(item["param_max"]),
            param_ideal=float(item["param_ideal"]),
            unit=str(item.get("unit", "")),
            extraction_confidence=float(item.get("extraction_confidence", 0.0)),
            source_type=source_type,
            action_type=item.get("action_type"),
        )

    def _merge_both(
        self, dim: str, v: dict, a: dict
    ) -> MergedPoint | ConflictItem:
        """Merge a visual+audio pair for the same dimension.

        Conflict rule: if |v_ideal - a_ideal| / max(|v_ideal|, 1e-6) > threshold
        the pair is treated as a conflict and NOT merged into the KB. Otherwise
        we produce a combined point (param ranges union'd, ideal averaged).
        """
        v_ideal = float(v["param_ideal"])
        a_ideal = float(a["param_ideal"])
        diff = abs(v_ideal - a_ideal) / max(abs(v_ideal), 1e-6)

        if diff > self.conflict_threshold_pct:
            return ConflictItem(
                dimension_name=dim,
                visual_value={
                    "min": float(v["param_min"]),
                    "max": float(v["param_max"]),
                    "ideal": v_ideal,
                    "unit": str(v.get("unit", "")),
                },
                audio_value={
                    "min": float(a["param_min"]),
                    "max": float(a["param_max"]),
                    "ideal": a_ideal,
                    "unit": str(a.get("unit", "")),
                },
                visual_confidence=float(v.get("extraction_confidence", 0.0)),
                audio_confidence=float(a.get("extraction_confidence", 0.0)),
                diff_pct=round(diff, 4),
                action_type=v.get("action_type") or a.get("action_type"),
            )

        return MergedPoint(
            dimension=dim,
            param_min=min(float(v["param_min"]), float(a["param_min"])),
            param_max=max(float(v["param_max"]), float(a["param_max"])),
            param_ideal=round((v_ideal + a_ideal) / 2.0, 4),
            unit=str(v.get("unit", "")),
            extraction_confidence=max(
                float(v.get("extraction_confidence", 0.0)),
                float(a.get("extraction_confidence", 0.0)),
            ),
            source_type="visual+audio",
            action_type=v.get("action_type") or a.get("action_type"),
        )
