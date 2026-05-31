"""Feature-023 — 术语归一化端到端集成测试.

T047:
  - test_merge_kb_normalize_cues_returns_metadata_for_colloquial
  - test_merge_kb_normalize_cues_returns_none_for_already_standard
  - test_merge_kb_normalize_cues_marks_pending_review_for_unknown
  - test_full_extract_to_normalize_pipeline (chained)

策略：直接调 merge_kb._normalize_cues + TerminologyNormalizer，
不启动 Celery，不需要真实 LLM（注入 mock）。
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from src.services.kb_extraction_pipeline.step_executors import merge_kb
from src.services.terminology_normalizer import TerminologyNormalizer


pytestmark = pytest.mark.asyncio


@dataclass
class FakeMergedPoint:
    """模拟 MergedPoint，仅含 _normalize_cues 需要的属性."""

    dimension: str
    action_type: str = "高吊弧圈球"


@pytest.fixture(autouse=True)
def reset_normalizer_singleton(monkeypatch):
    """每个测试前重置 _NORMALIZER 进程级单例，避免相互污染."""
    monkeypatch.setattr(merge_kb, "_NORMALIZER", None)
    yield
    monkeypatch.setattr(merge_kb, "_NORMALIZER", None)


def _inject_normalizer(monkeypatch, llm_client=None):
    """注入一个不依赖 settings 的 normalizer."""
    normalizer = TerminologyNormalizer(llm_client=llm_client)
    monkeypatch.setattr(merge_kb, "_NORMALIZER", normalizer)


# ── 用例 ────────────────────────────────────────────────────────────


async def test_merge_kb_normalize_cues_returns_metadata_for_colloquial(
    monkeypatch,
) -> None:
    """dimension 含静态映射口语 → conflict_detail.terminology 含 standard 字段."""
    _inject_normalizer(monkeypatch, llm_client=None)
    point = FakeMergedPoint(dimension="包住球")
    result = await merge_kb._normalize_cues(point)
    assert result is not None
    term = result["terminology"]
    assert term["original"] == "包住球"
    assert term["standard"] == "摩擦加厚"
    assert term["body_part"] == "racket"
    assert term["source"] == "static"
    assert term["pending_review"] is False


async def test_merge_kb_normalize_cues_returns_none_for_already_standard(
    monkeypatch,
) -> None:
    """dimension 已是标准术语 → _normalize_cues 返回 None（节省 JSONB 空间）."""
    _inject_normalizer(monkeypatch, llm_client=None)
    point = FakeMergedPoint(dimension="摩擦加厚")
    result = await merge_kb._normalize_cues(point)
    assert result is None


async def test_merge_kb_normalize_cues_marks_pending_review_for_unknown(
    monkeypatch,
) -> None:
    """dimension 未知且无 LLM → conflict_detail.terminology.pending_review=True."""
    _inject_normalizer(monkeypatch, llm_client=None)
    point = FakeMergedPoint(dimension="完全未知短语xyz")
    result = await merge_kb._normalize_cues(point)
    assert result is not None
    term = result["terminology"]
    assert term["pending_review"] is True
    assert term["original"] == "完全未知短语xyz"
    assert term["standard"] == "完全未知短语xyz"  # 保留原文


async def test_merge_kb_normalize_cues_substring_replacement(monkeypatch) -> None:
    """dimension 含口语子串 → 子串被替换 + body_part 保留."""
    _inject_normalizer(monkeypatch, llm_client=None)
    point = FakeMergedPoint(dimension="技术要点：包住球向前送")
    result = await merge_kb._normalize_cues(point)
    assert result is not None
    term = result["terminology"]
    assert "摩擦加厚" in term["standard"]
    assert "包住球" not in term["standard"]
    assert term["body_part"] == "racket"


async def test_normalizer_unavailable_returns_none(monkeypatch) -> None:
    """_NORMALIZER 标记为 False（不可用）时 _normalize_cues 返回 None."""
    monkeypatch.setattr(merge_kb, "_NORMALIZER", False)
    point = FakeMergedPoint(dimension="包住球")
    result = await merge_kb._normalize_cues(point)
    assert result is None


async def test_normalizer_exception_returns_none(monkeypatch) -> None:
    """normalizer.normalize 抛异常 → _normalize_cues 安全返回 None（不影响 merge）."""

    class _FailingNormalizer:
        async def normalize(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(merge_kb, "_NORMALIZER", _FailingNormalizer())
    point = FakeMergedPoint(dimension="包住球")
    result = await merge_kb._normalize_cues(point)
    assert result is None


async def test_normalizer_handles_empty_dimension(monkeypatch) -> None:
    """dimension 为空 → 返回 None，不调 normalizer."""
    _inject_normalizer(monkeypatch, llm_client=None)
    point = FakeMergedPoint(dimension="")
    result = await merge_kb._normalize_cues(point)
    assert result is None
