"""TechClassifier V2 — Feature-023 严格四级 + 字典强约束.

Phase 1: Keyword rule matching (config/tech_classification_rules.json).
         规则 value 为 action 名（56 行字典之一）；规则匹配后通过
         ActionDictionaryService 反查 (l1, l2, l3) 三级填充.
         若匹配的 action 在字典中存在跨手部重名（如「高吊弧圈球」），且无法
         通过课程系列名 / 文件名上下文唯一确定，降级为 LLM 兜底.

Phase 2: LLM fallback via LlmClient.chat(json_mode=True).
         Prompt 嵌入 56 行 (l1, l2, l3, action) enum 块；LLM 输出必须严格落在
         字典内，否则降级 unclassified.
         Confidence < 0.5 也降级 unclassified.

零兼容、不保留 aliases、不保留 classifier_version。物理删除旧 TECH_CATEGORIES /
_TECH_CATEGORY_LABELS / get_tech_label() 实现.

Usage:
  classifier = TechClassifier.from_settings()
  result = await classifier.classify(
      "22_正手拉下旋解析.mp4", "小孙专业乒乓球—全套正反手体系课程_33节"
  )
  print(result.category_l1, result.category_l2, result.category_l3, result.action)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from src.services.action_dictionary_service import (
    ActionDictionaryService,
    ActionEntry,
    get_action_dictionary_service,
)

logger = logging.getLogger(__name__)


# ── 常量 ────────────────────────────────────────────────────────────────
_UNCLASSIFIED = "unclassified"

_LLM_PROMPT_TEMPLATE = """\
你是一位乒乓球技术分类专家。根据以下视频文件名和所属课程系列，从给定的 56 行
技术动作字典中选出最匹配的一项。

课程系列：{course_series}
视频文件名：{filename}

可选技术动作（4 列结构：握拍方式 | 胶皮类型 | 手部技术·技术大类 | 具体动作名）：
{action_dict_block}

请以严格 JSON 格式回答（必须四级齐全且与上方字典完全匹配，不得发明新值）：
{{"category_l1": "<l1>", "category_l2": "<l2>", "category_l3": "<l3>", "action": "<动作名>", "confidence": 0.0, "reason": "一句话说明"}}

如果文件名/系列名不足以判断，返回 confidence=0.0，但仍需挑选一项最接近的字典项；
调用方会在 confidence < 0.5 时降级为 unclassified。

只输出 JSON，不要其他内容。"""


@dataclass
class ClassificationResultV2:
    """Feature-023 四级分类结果（不可变值对象，跨 service 安全传递）."""

    category_l1: str | None = None
    category_l2: str | None = None
    category_l3: str | None = None
    action: str = _UNCLASSIFIED
    tech_tags: list[str] = field(default_factory=list)
    raw_tech_desc: Optional[str] = None
    classification_source: str = "rule"  # rule | llm | manual
    confidence: float = 1.0

    @property
    def is_unclassified(self) -> bool:
        return self.action == _UNCLASSIFIED


# ── 向后兼容别名（仅保留类型名，旧字段语义已物理删除）────────────────────
# `ClassificationResult` 旧名仍保留，便于现有调用方一次性 import 切换；
# 实际类型即 `ClassificationResultV2`，无 tech_category 字段
ClassificationResult = ClassificationResultV2


class TechClassifier:
    """Feature-023 V2：keyword 匹配 + LLM 兜底，输出严格四级 + 字典约束.

    保留模块路径与类名 `TechClassifier` 以避免上游导入路径变动；实现整体重写.
    """

    LOW_CONFIDENCE_THRESHOLD = 0.5

    def __init__(
        self,
        *,
        rules_path: str,
        action_dict: ActionDictionaryService,
        llm_client=None,
    ) -> None:
        self.llm_client = llm_client
        self._action_dict = action_dict
        with open(rules_path, encoding="utf-8") as f:
            # rules_path 期望结构: {"<action_name>": ["关键词1", "关键词2", ...]}
            # 注意：rules 中的 key 期望已与 tech_actions 字典中的 action 字段对齐
            self._rules: dict[str, list[str]] = json.load(f)

    @classmethod
    def from_settings(cls) -> "TechClassifier":
        """Create from project settings — loads default config paths."""
        from src.services.llm_client import LlmClient

        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        rules_path = os.path.join(base_dir, "config", "tech_classification_rules.json")
        llm_client = LlmClient.from_settings()
        action_dict = get_action_dictionary_service()
        return cls(
            rules_path=rules_path,
            action_dict=action_dict,
            llm_client=llm_client,
        )

    # ────────────────────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────────────────────
    async def classify(
        self, filename: str, course_series: str
    ) -> ClassificationResultV2:
        """Classify a video file into a four-level dictionary entry.

        Args:
            filename: Video filename (basename, e.g. "22_正手拉下旋解析.mp4")
            course_series: COS directory name (course series)

        Returns:
            ClassificationResultV2 with l1/l2/l3/action + confidence + source.
            unclassified result keeps l1/l2/l3=None.
        """
        # Phase 1: keyword rule matching
        rule_match = await self._match_rules(filename)
        if rule_match is not None:
            return rule_match

        # Phase 2: LLM fallback
        if self.llm_client is not None:
            return await self._classify_with_llm(filename, course_series)

        # No rule match, no LLM client → unclassified
        return ClassificationResultV2(
            action=_UNCLASSIFIED,
            classification_source="rule",
            confidence=1.0,
        )

    # ────────────────────────────────────────────────────────────────
    # Phase 1: 关键词匹配
    # ────────────────────────────────────────────────────────────────
    async def _match_rules(self, filename: str) -> ClassificationResultV2 | None:
        """Scan rules in order; return first match as primary action.

        Rule key 形如 `"高吊弧圈球"` 或 `"forehand_topspin"`（旧 21 类 ID）。
        Feature-023 期望 rules JSON 已升级为新字典 action 名；旧格式过渡期：
        若 key 不在字典，则跳过（不致命）。
        """
        primary_action: str | None = None
        primary_keyword: str | None = None
        extra_tags: list[str] = []

        for rule_key, keywords in self._rules.items():
            # 防御性校验 1：跳过下划线开头的元数据 key（如 "_comment"、"_meta"）
            if rule_key.startswith("_"):
                continue
            # 防御性校验 2：keywords 必须是 list；字符串会被 Python 按字符迭代造成误判
            if not isinstance(keywords, list):
                logger.warning(
                    "rule key %r has non-list keywords (got %s); skipping to avoid "
                    "char-by-char iteration false positives",
                    rule_key,
                    type(keywords).__name__,
                )
                continue
            for kw in keywords:
                if kw in filename:
                    if primary_action is None:
                        primary_action = rule_key
                        primary_keyword = kw
                    elif rule_key != primary_action and rule_key not in extra_tags:
                        extra_tags.append(rule_key)
                    break

        if primary_action is None:
            return None

        # 反查字典：rule_key 必须能落到字典里（否则当作未命中）
        candidates = await self._action_dict.lookup_candidates(primary_action)
        if not candidates:
            logger.warning(
                "rule key %r matched filename %r but not found in tech_actions dict; "
                "falling through to LLM",
                primary_action,
                filename,
            )
            return None

        chosen = self._disambiguate_by_filename(candidates, filename)
        if chosen is None:
            # 无法在文件名/系列名中找到歧义解析依据 → fall through 到 LLM
            logger.info(
                "rule action %r has %d cross-hand variants; cannot disambiguate from "
                "filename %r, falling back to LLM",
                primary_action,
                len(candidates),
                filename,
            )
            return None

        return ClassificationResultV2(
            category_l1=chosen.category_l1,
            category_l2=chosen.category_l2,
            category_l3=chosen.category_l3,
            action=chosen.action,
            tech_tags=extra_tags,
            raw_tech_desc=primary_keyword,
            classification_source="rule",
            confidence=1.0,
        )

    @staticmethod
    def _disambiguate_by_filename(
        candidates: list[ActionEntry], filename: str
    ) -> ActionEntry | None:
        """跨手部重名场景：通过文件名中的 "正手"/"反手" 关键字消歧."""
        if len(candidates) == 1:
            return candidates[0]
        # 多候选场景，尝试依据文件名中的手部关键词消歧
        if "反手" in filename:
            for c in candidates:
                if "反手" in c.category_l3:
                    return c
        if "正手" in filename:
            for c in candidates:
                if "正手" in c.category_l3:
                    return c
        return None

    # ────────────────────────────────────────────────────────────────
    # Phase 2: LLM 兜底
    # ────────────────────────────────────────────────────────────────
    async def _classify_with_llm(
        self, filename: str, course_series: str
    ) -> ClassificationResultV2:
        """Call LLM with strict dictionary enum constraint."""
        action_dict_block = await self._action_dict.get_prompt_enum_block()
        prompt = _LLM_PROMPT_TEMPLATE.format(
            course_series=course_series,
            filename=filename,
            action_dict_block=action_dict_block,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            response_text, _tokens = self.llm_client.chat(
                messages, temperature=0.0, json_mode=True
            )
            data = json.loads(response_text)
            l1 = (data.get("category_l1") or "").strip()
            l2 = (data.get("category_l2") or "").strip()
            l3 = (data.get("category_l3") or "").strip()
            action = (data.get("action") or "").strip()
            confidence = float(data.get("confidence", 0.0))

            # 字典强约束：四元组必须落在 tech_actions 字典内
            if not await self._action_dict.validate(l1, l2, l3, action):
                logger.warning(
                    "LLM returned non-dictionary quad (%r,%r,%r,%r) for filename=%r, "
                    "degrading to unclassified",
                    l1, l2, l3, action, filename,
                )
                return ClassificationResultV2(
                    action=_UNCLASSIFIED,
                    classification_source="llm",
                    confidence=0.0,
                )

            # 低置信度降级
            if confidence < self.LOW_CONFIDENCE_THRESHOLD:
                return ClassificationResultV2(
                    action=_UNCLASSIFIED,
                    classification_source="llm",
                    confidence=confidence,
                )

            return ClassificationResultV2(
                category_l1=l1,
                category_l2=l2,
                category_l3=l3,
                action=action,
                tech_tags=[],
                raw_tech_desc=None,
                classification_source="llm",
                confidence=confidence,
            )
        except Exception as exc:
            logger.error(
                "LLM classification failed for filename=%r: %s", filename, exc
            )
            return ClassificationResultV2(
                action=_UNCLASSIFIED,
                classification_source="llm",
                confidence=0.0,
            )


__all__ = [
    "ClassificationResultV2",
    "ClassificationResult",  # backward-compat alias
    "TechClassifier",
]
