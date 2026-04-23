"""TechClassifier — two-phase table tennis technique classification.

Phase 1: Keyword rule matching (config/tech_classification_rules.json).
         Rules are ordered — more specific rules first (e.g. forehand_topspin_backspin
         before forehand_topspin).

Phase 2: LLM fallback via LlmClient.chat() when no rule matches.
         Confidence < 0.5 degrades to 'unclassified'.

Usage:
  classifier = TechClassifier.from_settings()
  result = classifier.classify("22_正手拉下旋解析.mp4", "小孙专业乒乓球—全套正反手体系课程_33节")
  print(result.tech_category, result.classification_source, result.confidence)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Canonical list of valid tech_category IDs (application-layer enum).
TECH_CATEGORIES: list[str] = [
    "forehand_push_long",
    "forehand_attack",
    "forehand_topspin",
    "forehand_topspin_backspin",
    "forehand_loop_fast",
    "forehand_loop_high",
    "forehand_flick",
    "backhand_attack",
    "backhand_topspin",
    "backhand_topspin_backspin",
    "backhand_flick",
    "backhand_push",
    "serve",
    "receive",
    "footwork",
    "forehand_backhand_transition",
    "defense",
    "penhold_reverse",
    "stance_posture",
    "general",
    "unclassified",
]

_TECH_CATEGORY_LABELS: dict[str, str] = {
    "forehand_push_long": "正手劈长",
    "forehand_attack": "正手攻球",
    "forehand_topspin": "正手拉球/上旋",
    "forehand_topspin_backspin": "正手拉下旋",
    "forehand_loop_fast": "正手前冲弧圈",
    "forehand_loop_high": "正手高调弧圈",
    "forehand_flick": "正手拧拉/台内挑打",
    "backhand_attack": "反手攻球",
    "backhand_topspin": "反手拉球/上旋",
    "backhand_topspin_backspin": "反手拉下旋",
    "backhand_flick": "反手弹击/快撕",
    "backhand_push": "反手推挡/搓球",
    "serve": "发球",
    "receive": "接发球",
    "footwork": "步法",
    "forehand_backhand_transition": "正反手转换",
    "defense": "防守",
    "penhold_reverse": "直拍横打",
    "stance_posture": "站位/姿态",
    "general": "综合/通用",
    "unclassified": "待分类",
}


def get_tech_label(tech_category: str) -> str:
    """Return Chinese display label for a tech_category ID."""
    return _TECH_CATEGORY_LABELS.get(tech_category, tech_category)


_LLM_PROMPT_TEMPLATE = """\
你是一位乒乓球技术分类专家。根据以下视频文件名和所属课程系列，判断该视频教学的主要技术类别。

课程系列：{course_series}
视频文件名：{filename}

可选技术类别（从中选择一个最匹配的 ID）：
{tech_category_list}

请以 JSON 格式回答：
{{"tech_category": "<类别ID>", "confidence": 0.0, "reason": "一句话说明"}}

只输出 JSON，不要其他内容。"""


@dataclass
class ClassificationResult:
    tech_category: str
    tech_tags: list[str] = field(default_factory=list)
    raw_tech_desc: Optional[str] = None
    classification_source: str = "rule"  # rule | llm | manual
    confidence: float = 1.0


class TechClassifier:
    """Classify a video filename into a tech_category using keyword rules + LLM fallback."""

    def __init__(
        self,
        *,
        rules_path: str,
        llm_client=None,
    ) -> None:
        self.llm_client = llm_client
        with open(rules_path, encoding="utf-8") as f:
            self._rules: dict[str, list[str]] = json.load(f)

    @classmethod
    def from_settings(cls) -> "TechClassifier":
        """Create from project settings — loads default config paths."""
        from src.services.llm_client import LlmClient

        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))
        rules_path = os.path.join(base_dir, "config", "tech_classification_rules.json")
        llm_client = LlmClient.from_settings()
        return cls(rules_path=rules_path, llm_client=llm_client)

    def classify(self, filename: str, course_series: str) -> ClassificationResult:
        """Classify a video file into a tech_category.

        Args:
            filename: Video filename (basename only, e.g. "22_正手拉下旋解析.mp4")
            course_series: COS directory name (course series)

        Returns:
            ClassificationResult with tech_category, tech_tags, source, confidence
        """
        # Phase 1: keyword rule matching
        result = self._match_rules(filename)
        if result is not None:
            return result

        # Phase 2: LLM fallback
        if self.llm_client is not None:
            return self._classify_with_llm(filename, course_series)

        # No rule match, no LLM
        return ClassificationResult(
            tech_category="unclassified",
            classification_source="rule",
            confidence=1.0,
        )

    def _match_rules(self, filename: str) -> Optional[ClassificationResult]:
        """Scan rules in order; return first match as primary, continue for tech_tags."""
        primary_category: Optional[str] = None
        primary_keyword: Optional[str] = None
        extra_tags: list[str] = []

        for category, keywords in self._rules.items():
            for kw in keywords:
                if kw in filename:
                    if primary_category is None:
                        primary_category = category
                        primary_keyword = kw
                    elif category != primary_category and category not in extra_tags:
                        extra_tags.append(category)
                    break  # one keyword hit per category is enough

        if primary_category is None:
            return None

        return ClassificationResult(
            tech_category=primary_category,
            tech_tags=extra_tags,
            raw_tech_desc=primary_keyword,
            classification_source="rule",
            confidence=1.0,
        )

    def _classify_with_llm(self, filename: str, course_series: str) -> ClassificationResult:
        """Call LLM for fallback classification."""
        valid_cats = [c for c in TECH_CATEGORIES if c != "unclassified"]
        prompt = _LLM_PROMPT_TEMPLATE.format(
            course_series=course_series,
            filename=filename,
            tech_category_list="\n".join(f"- {c}" for c in valid_cats),
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            response_text, _tokens = self.llm_client.chat(
                messages, temperature=0.0, json_mode=True
            )
            data = json.loads(response_text)
            tech_category = data.get("tech_category", "unclassified")
            confidence = float(data.get("confidence", 0.0))

            # Validate returned category
            if tech_category not in TECH_CATEGORIES:
                logger.warning(
                    "LLM returned invalid tech_category=%r for filename=%r, "
                    "degrading to unclassified",
                    tech_category, filename,
                )
                tech_category = "unclassified"
                confidence = 0.0

            # Degrade low-confidence to unclassified
            if confidence < 0.5:
                tech_category = "unclassified"

            return ClassificationResult(
                tech_category=tech_category,
                tech_tags=[],
                raw_tech_desc=None,
                classification_source="llm",
                confidence=confidence,
            )
        except Exception as exc:
            logger.error(
                "LLM classification failed for filename=%r: %s", filename, exc
            )
            return ClassificationResult(
                tech_category="unclassified",
                classification_source="llm",
                confidence=0.0,
            )
