"""TerminologyNormalizer — Feature-023 US4 教练口语 → 标准术语归一化.

双层降级策略:
  1. **静态映射**（config/terminology_mapping.json）：O(1) 完全匹配 / 子串匹配
  2. **LLM 兜底**：当静态未命中时，调用 LlmClient.chat() JSON 模式给出标准术语 +
     置信度；若 confidence < 0.7 标记 pending_review=True 留人工复核

设计要点:
  - 原口语永远保留到 ``cue_words`` 字段，禁止丢失
  - normalize() 是 idempotent 的：传入已是标准术语（在 standard_terms 集合中）时
    直接返回，不二次调用 LLM
  - 缓存模式：进程级 _MAPPING_CACHE 在首次实例化时加载，避免每次 normalize 读盘
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


_LOW_CONFIDENCE_THRESHOLD = 0.7

_MAPPING_CACHE: dict[str, dict] | None = None  # filepath → loaded dict
_DEFAULT_MAPPING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "terminology_mapping.json",
)


@dataclass(frozen=True)
class NormalizationResult:
    """术语归一化结果."""

    original: str  # 口语原文（必填，永不丢失）
    standard_term: str  # 标准术语；命中失败时回退为 original
    body_part: Optional[str] = None  # 静态表自带；LLM 兜底可为 None
    confidence: float = 1.0  # 静态命中=1.0；LLM 由响应给出
    source: str = "static"  # static | llm | unchanged
    pending_review: bool = False  # confidence < 0.7 时 True

    @property
    def normalized(self) -> bool:
        """是否真正发生了归一化（standard_term != original）."""
        return self.standard_term != self.original


def _load_mapping(path: str = _DEFAULT_MAPPING_PATH) -> dict:
    """加载静态映射，进程级缓存."""
    global _MAPPING_CACHE
    if _MAPPING_CACHE is None:
        _MAPPING_CACHE = {}
    if path not in _MAPPING_CACHE:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # 构造 colloquial → entry 索引
        index: dict[str, dict] = {}
        standards: set[str] = set()
        for entry in data.get("mappings", []):
            colloq = entry.get("colloquial", "")
            standard = entry.get("standard", "")
            if not colloq or not standard:
                continue
            index[colloq] = {
                "standard": standard,
                "body_part": entry.get("body_part"),
            }
            standards.add(standard)
        _MAPPING_CACHE[path] = {
            "version": data.get("version", "v1"),
            "index": index,
            "standards": standards,
        }
    return _MAPPING_CACHE[path]


_LLM_PROMPT_TEMPLATE = """\
你是一位乒乓球技术术语标准化专家。请把下列教练口语短语映射为通用标准术语。

口语短语：{phrase}
（可能的）身体部位上下文：{body_part_hint}

约束：
- 必须输出一个简洁的标准术语（≤10 字），使用通用解剖学/力学描述
- 给出 0.0-1.0 的置信度；不确定时写 0.0
- 不要输出口语原文；如确实无法判断，写 standard_term=原文 + confidence=0.0

请以 JSON 格式回答：
{{"standard_term": "<标准术语>", "body_part": "<身体部位英文 key>", "confidence": 0.0, "reason": "一句话"}}
只输出 JSON。"""


class TerminologyNormalizer:
    """教练口语 → 标准术语 归一化器（静态优先 + LLM 兜底）."""

    def __init__(
        self,
        *,
        mapping_path: str = _DEFAULT_MAPPING_PATH,
        llm_client=None,
    ) -> None:
        self._mapping = _load_mapping(mapping_path)
        self._llm_client = llm_client

    @classmethod
    def from_settings(cls) -> "TerminologyNormalizer":
        """工厂方法：从项目配置初始化（含默认 mapping + LlmClient）."""
        from src.services.llm_client import LlmClient

        try:
            llm = LlmClient.from_settings()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "TerminologyNormalizer: LLM unconfigured (%s); LLM fallback disabled",
                exc,
            )
            llm = None
        return cls(llm_client=llm)

    # ────────────────────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────────────────────
    async def normalize(
        self, phrase: str, *, body_part_hint: str | None = None
    ) -> NormalizationResult:
        """对一条口语短语执行归一化.

        Args:
            phrase: 口语原文
            body_part_hint: 可选身体部位线索（提升 LLM 准确率）

        Returns:
            NormalizationResult；original 字段永远等于入参 phrase
        """
        if not phrase or not phrase.strip():
            return NormalizationResult(
                original=phrase, standard_term=phrase, source="unchanged"
            )

        # 1. 已是标准术语 → 直接返回（idempotent）
        if phrase in self._mapping["standards"]:
            return NormalizationResult(
                original=phrase,
                standard_term=phrase,
                confidence=1.0,
                source="unchanged",
            )

        # 2. 静态完全匹配
        index = self._mapping["index"]
        if phrase in index:
            entry = index[phrase]
            return NormalizationResult(
                original=phrase,
                standard_term=entry["standard"],
                body_part=entry["body_part"],
                confidence=1.0,
                source="static",
            )

        # 3. 静态子串匹配（口语短语常嵌在长句中）
        for colloq, entry in index.items():
            if colloq in phrase:
                return NormalizationResult(
                    original=phrase,
                    standard_term=phrase.replace(colloq, entry["standard"]),
                    body_part=entry["body_part"],
                    confidence=0.95,
                    source="static",
                )

        # 4. LLM 兜底
        if self._llm_client is None:
            # 无 LLM 客户端：保留原文，低置信度，标记 pending_review
            return NormalizationResult(
                original=phrase,
                standard_term=phrase,
                confidence=0.0,
                source="unchanged",
                pending_review=True,
            )

        return await self._llm_normalize(phrase, body_part_hint)

    # ────────────────────────────────────────────────────────────────
    # 内部：LLM 调用
    # ────────────────────────────────────────────────────────────────
    async def _llm_normalize(
        self, phrase: str, body_part_hint: str | None
    ) -> NormalizationResult:
        prompt = _LLM_PROMPT_TEMPLATE.format(
            phrase=phrase,
            body_part_hint=body_part_hint or "未知",
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            response_text, _tokens = self._llm_client.chat(
                messages, temperature=0.0, json_mode=True
            )
            data = json.loads(response_text)
            standard = (data.get("standard_term") or phrase).strip() or phrase
            body_part = data.get("body_part") or body_part_hint
            confidence = float(data.get("confidence", 0.0))
            pending = confidence < _LOW_CONFIDENCE_THRESHOLD
            return NormalizationResult(
                original=phrase,
                standard_term=standard if not pending else phrase,
                body_part=body_part,
                confidence=confidence,
                source="llm",
                pending_review=pending,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "TerminologyNormalizer LLM fallback failed for phrase=%r: %s",
                phrase, exc,
            )
            return NormalizationResult(
                original=phrase,
                standard_term=phrase,
                confidence=0.0,
                source="unchanged",
                pending_review=True,
            )


__all__ = [
    "NormalizationResult",
    "TerminologyNormalizer",
]
