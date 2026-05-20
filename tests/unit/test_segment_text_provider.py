"""Feature-021 T028 — segment_text_provider 切片对齐单测。"""

from __future__ import annotations

import pytest

from src.services.curation.segment_text_provider import (
    extract_segment_text,
    iter_segment_texts,
)


def _sample_sentences() -> list[dict]:
    """构造一段覆盖 0-30s 的转录，每 5 秒一句."""
    return [
        {"start": 0.0,  "end": 5.0,  "text": "段一", "confidence": 0.9},
        {"start": 5.0,  "end": 10.0, "text": "段二", "confidence": 0.9},
        {"start": 10.0, "end": 15.0, "text": "段三", "confidence": 0.9},
        {"start": 15.0, "end": 20.0, "text": "段四", "confidence": 0.9},
        {"start": 20.0, "end": 25.0, "text": "段五", "confidence": 0.9},
        {"start": 25.0, "end": 30.0, "text": "段六", "confidence": 0.9},
    ]


def test_extract_strict_window() -> None:
    """[5s, 15s) 窗口应覆盖段二（5-10s）+ 段三（10-15s）."""
    out = extract_segment_text(
        _sample_sentences(),
        segment_start_ms=5000,
        segment_end_ms=15000,
    )
    assert out == "段二 段三"


def test_extract_partial_overlap_at_left_edge() -> None:
    """[3s, 7s) 与段一（0-5s）和段二（5-10s）都有交叉。"""
    out = extract_segment_text(
        _sample_sentences(),
        segment_start_ms=3000,
        segment_end_ms=7000,
    )
    assert out == "段一 段二"


def test_extract_no_overlap_returns_empty() -> None:
    out = extract_segment_text(
        _sample_sentences(),
        segment_start_ms=100_000,
        segment_end_ms=110_000,
    )
    assert out == ""


def test_extract_skips_non_dict_items() -> None:
    """sentences 列表混入 None / str / 缺字段 dict 时静默跳过."""
    sentences = [
        None,
        "garbage",
        {"start": 0.0, "end": 5.0, "text": "good"},
        {"start": "bad", "end": 5.0, "text": "skip"},  # start 类型错
        {"start": 0.0, "end": 5.0},                    # 缺 text
        {"start": 0.0, "end": 5.0, "text": "   "},     # 空 text
    ]
    out = extract_segment_text(
        sentences, segment_start_ms=0, segment_end_ms=5000
    )
    assert out == "good"


def test_extract_invalid_window_raises() -> None:
    with pytest.raises(ValueError):
        extract_segment_text([], segment_start_ms=10, segment_end_ms=10)
    with pytest.raises(ValueError):
        extract_segment_text([], segment_start_ms=10, segment_end_ms=5)


def test_iter_segment_texts_batch() -> None:
    sentences = _sample_sentences()
    ranges = [(0, 10000), (10000, 20000), (20000, 30000)]
    out = iter_segment_texts(sentences, segment_ranges_ms=ranges)
    assert out == ["段一 段二", "段三 段四", "段五 段六"]


def test_empty_sentences_returns_empty() -> None:
    assert extract_segment_text([], segment_start_ms=0, segment_end_ms=10000) == ""
