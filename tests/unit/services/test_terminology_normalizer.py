"""Feature-023 — TerminologyNormalizer 单元测试.

T046:
  - test_static_mapping_hit
  - test_static_mapping_substring
  - test_already_standard_returns_unchanged
  - test_llm_fallback_high_confidence
  - test_llm_fallback_low_confidence_marks_pending_review
  - test_no_llm_marks_pending_review
  - test_original_preserved_in_result
  - test_idempotent_normalize_does_not_call_llm_twice
  - test_coverage_rate_meets_80pct
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.services.terminology_normalizer import (
    NormalizationResult,
    TerminologyNormalizer,
)


pytestmark = pytest.mark.asyncio


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def llm_high_conf():
    mock = MagicMock()
    mock.chat.return_value = (
        json.dumps(
            {
                "standard_term": "前臂内旋发力",
                "body_part": "forearm",
                "confidence": 0.85,
                "reason": "test",
            }
        ),
        50,
    )
    return mock


@pytest.fixture
def llm_low_conf():
    mock = MagicMock()
    mock.chat.return_value = (
        json.dumps(
            {
                "standard_term": "前臂内旋发力",
                "body_part": "forearm",
                "confidence": 0.4,
                "reason": "test",
            }
        ),
        50,
    )
    return mock


# ── 静态命中 ─────────────────────────────────────────────────────────


async def test_static_mapping_hit() -> None:
    """完全匹配静态映射 → standard_term 替换、source='static'、confidence=1.0."""
    normalizer = TerminologyNormalizer(llm_client=None)
    result = await normalizer.normalize("包住球")
    assert isinstance(result, NormalizationResult)
    assert result.standard_term == "摩擦加厚"
    assert result.body_part == "racket"
    assert result.confidence == 1.0
    assert result.source == "static"
    assert result.original == "包住球"
    assert result.pending_review is False


async def test_static_mapping_substring() -> None:
    """口语嵌在长句中 → 子串替换，confidence=0.95."""
    normalizer = TerminologyNormalizer(llm_client=None)
    result = await normalizer.normalize("击球时要包住球向前送")
    assert "摩擦加厚" in result.standard_term
    assert "包住球" not in result.standard_term  # 已替换
    assert result.confidence == 0.95
    assert result.source == "static"


async def test_already_standard_returns_unchanged() -> None:
    """已是标准术语（如『摩擦加厚』）→ 直接返回 unchanged，不调 LLM."""
    llm_should_not_be_called = MagicMock()
    llm_should_not_be_called.chat.side_effect = AssertionError("LLM 不应被调用")
    normalizer = TerminologyNormalizer(llm_client=llm_should_not_be_called)
    result = await normalizer.normalize("摩擦加厚")
    assert result.source == "unchanged"
    assert result.standard_term == "摩擦加厚"
    assert result.normalized is False


# ── LLM 兜底 ─────────────────────────────────────────────────────────


async def test_llm_fallback_high_confidence(llm_high_conf) -> None:
    """静态未命中 + LLM 高置信度 → 用 LLM 输出，pending_review=False."""
    normalizer = TerminologyNormalizer(llm_client=llm_high_conf)
    result = await normalizer.normalize("某个未在映射表的口语")
    assert result.source == "llm"
    assert result.standard_term == "前臂内旋发力"
    assert result.confidence == 0.85
    assert result.pending_review is False


async def test_llm_fallback_low_confidence_marks_pending_review(llm_low_conf) -> None:
    """LLM 置信度 < 0.7 → standard_term 保留原文，pending_review=True."""
    normalizer = TerminologyNormalizer(llm_client=llm_low_conf)
    result = await normalizer.normalize("某个未在映射表的口语")
    assert result.source == "llm"
    assert result.confidence == 0.4
    assert result.pending_review is True
    # 低置信度时不强制替换 → 保留原文
    assert result.standard_term == "某个未在映射表的口语"


async def test_no_llm_marks_pending_review() -> None:
    """无 LLM 客户端 + 静态未命中 → pending_review=True，原文保留."""
    normalizer = TerminologyNormalizer(llm_client=None)
    result = await normalizer.normalize("完全未知的奇怪短语")
    assert result.pending_review is True
    assert result.standard_term == "完全未知的奇怪短语"
    assert result.source == "unchanged"


# ── 不变量 ────────────────────────────────────────────────────────


async def test_original_preserved_in_result() -> None:
    """无论命中/未命中、LLM 是否被调用，original 字段必须等于入参."""
    normalizer = TerminologyNormalizer(llm_client=None)
    cases = ["包住球", "摩擦加厚", "完全未知短语", "击球时要包住球", ""]
    for phrase in cases:
        result = await normalizer.normalize(phrase)
        assert result.original == phrase, f"original 字段被改写: {phrase!r}"


async def test_empty_phrase_returns_unchanged() -> None:
    """空字符串 → unchanged，不抛异常."""
    normalizer = TerminologyNormalizer(llm_client=None)
    result = await normalizer.normalize("")
    assert result.standard_term == ""
    assert result.source == "unchanged"


# ── 覆盖率（SC-004 ≥ 80%）────────────────────────────────────────────


async def test_coverage_rate_meets_80pct() -> None:
    """对 25 条覆盖样本（其中 20 条静态命中、5 条未知）→ 静态覆盖率 ≥ 80%."""
    samples = [
        # 20 条命中
        "包住球", "兜住球", "贴住球", "顶住球", "撞击为主",
        "蹭一下", "甩起来", "甩鞭子", "鞭打", "压住手腕",
        "锁住手腕", "扣手腕", "甩手腕", "蹬腿", "撑住腿",
        "压腿", "沉腰", "送髋", "转腰", "扭腰",
        # 5 条未知
        "随便瞎说", "abc", "ZZZ", "毫无关系", "测试",
    ]
    normalizer = TerminologyNormalizer(llm_client=None)
    static_hits = 0
    for s in samples:
        result = await normalizer.normalize(s)
        if result.source == "static":
            static_hits += 1
    coverage = static_hits / len(samples)
    assert coverage >= 0.8, f"静态命中率 {coverage:.2%} 低于 80% (SC-004)"
