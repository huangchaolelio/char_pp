"""VideoClassifierService — keyword-based classification of COS teaching videos.

Loads classification rules from ``src/config/video_classification.yaml`` and
matches each video's filename against the configured keyword rules in priority
order (first matching rule wins).

Confidence levels:
    1.0 — precise keyword match to a leaf category node
    0.7 — category-level match (require_keywords hit, no sub-category keyword)
    0.5 — fallback "other" category (no match at all)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Default path relative to this file's location
_DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "video_classification.yaml"


@dataclass
class VideoClassificationResult:
    """Classification result for a single video."""

    cos_object_key: str
    coach_name: str
    tech_category: str
    tech_sub_category: Optional[str]
    tech_detail: Optional[str]
    video_type: str  # "tutorial" | "training"
    action_type: Optional[str]
    classification_confidence: float


class VideoClassifierService:
    """Classifies COS teaching videos using keyword-based rules from a YAML config.

    Usage::

        service = VideoClassifierService()
        result = service.classify("charhuang/tt_video/.../第06节正手攻球.mp4")
        results = service.classify_all(list_videos())
    """

    def __init__(self, config_path: Path = _DEFAULT_CONFIG) -> None:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self._categories: list[dict] = config.get("categories", [])
        self._coaches: list[dict] = config.get("coaches", [])

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def classify(self, cos_object_key: str) -> VideoClassificationResult:
        """Return classification for a single COS object key."""
        filename = cos_object_key.split("/")[-1]
        coach_name = self._infer_coach(cos_object_key)
        video_type = "training" if "训练计划" in filename or "训练方法" in filename else "tutorial"

        for rule in self._categories:
            result = self._match_rule(rule, filename)
            if result is not None:
                cat, sub, detail, action_type, confidence = result
                return VideoClassificationResult(
                    cos_object_key=cos_object_key,
                    coach_name=coach_name,
                    tech_category=cat,
                    tech_sub_category=sub,
                    tech_detail=detail,
                    video_type=video_type,
                    action_type=action_type,
                    classification_confidence=confidence,
                )

        # Should never reach here — misc_other rule is a catch-all with empty match_keywords
        logger.warning("No rule matched for %s; falling back to 其他", filename)
        return VideoClassificationResult(
            cos_object_key=cos_object_key,
            coach_name=coach_name,
            tech_category="其他",
            tech_sub_category=None,
            tech_detail=None,
            video_type=video_type,
            action_type=None,
            classification_confidence=0.5,
        )

    def classify_all(self, videos: list[dict]) -> list[VideoClassificationResult]:
        """Classify a list of video dicts (each must have ``cos_object_key`` key)."""
        return [self.classify(v["cos_object_key"]) for v in videos]

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _infer_coach(self, cos_object_key: str) -> str:
        """Return coach name by matching COS path against configured coach keywords."""
        for coach in self._coaches:
            for kw in coach.get("cos_prefix_keywords", []):
                if kw in cos_object_key:
                    return coach["name"]
        return "未知"

    def _match_rule(
        self, rule: dict, filename: str
    ) -> Optional[tuple[str, Optional[str], Optional[str], Optional[str], float]]:
        """Return (tech_category, sub, detail, action_type, confidence) or None."""
        exclude_kws: list[str] = rule.get("exclude_keywords", [])
        require_kws: list[str] = rule.get("require_keywords", [])
        match_kws: list[str] = rule.get("match_keywords", [])

        # Exclude check (highest priority)
        if any(kw in filename for kw in exclude_kws):
            return None

        # Require check (all must be present)
        if not all(kw in filename for kw in require_kws):
            return None

        # Match check (at least one, or empty list = auto-match)
        if match_kws and not any(kw in filename for kw in match_kws):
            return None

        confidence: float = float(rule.get("confidence", 1.0))
        return (
            rule["tech_category"],
            rule.get("tech_sub_category"),
            rule.get("tech_detail"),
            rule.get("action_type"),
            confidence,
        )
