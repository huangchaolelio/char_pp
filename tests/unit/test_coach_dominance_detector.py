"""Feature-021 T027 — coach_dominance_detector 启发式判定单测。"""

from __future__ import annotations

import pytest

from src.services.curation.coach_dominance_detector import estimate_dominance_ratio


def test_empty_text_returns_neutral() -> None:
    assert estimate_dominance_ratio(segment_text="", target_coach_name="张继科") == 0.5
    assert estimate_dominance_ratio(segment_text="   ", target_coach_name="张继科") == 0.5


def test_anti_dominance_keyword_zeroes_out() -> None:
    """采访 / 颁奖 / 解说员等关键词命中即重罚到 0.0。"""
    for kw in ("接受采访", "记者", "颁奖", "解说员"):
        text = f"今天是{kw}的现场"
        ratio = estimate_dominance_ratio(segment_text=text, target_coach_name="张继科")
        assert ratio == 0.0, f"keyword {kw!r} should drive ratio to 0"


def test_teaching_text_increases_ratio_above_baseline() -> None:
    """含 "你 / 大家 / 看 / 注意 / 拉 / 转" 等教学口吻关键词 → ratio > 0.5。"""
    text = "你看这个动作要领，大家注意拉球的瞬间，跟着我转腰，重心要压低"
    ratio = estimate_dominance_ratio(segment_text=text, target_coach_name="张继科")
    assert ratio > 0.5


def test_repeated_coach_name_lowers_ratio() -> None:
    """姓名 ≥3 次出现 → 第三人称介绍信号 → 扣分."""
    # 使用极简文本，避免教学动词把 baseline 顶满到 1.0 后看不出姓名扣分。
    text_with_name = "张继科 张继科 张继科"
    text_neutral = "他 他 他"
    ratio_with = estimate_dominance_ratio(
        segment_text=text_with_name, target_coach_name="张继科"
    )
    ratio_neutral = estimate_dominance_ratio(
        segment_text=text_neutral, target_coach_name="张继科"
    )
    # 反向比较：含姓名版必须严格更低（扣了 name_penalty）
    assert ratio_with < ratio_neutral


def test_none_coach_name_does_not_crash() -> None:
    """target_coach_name=None 时不做姓名扣分仍能算。"""
    ratio = estimate_dominance_ratio(
        segment_text="大家看这个动作", target_coach_name=None
    )
    assert 0.0 <= ratio <= 1.0


def test_too_long_or_short_name_skips_penalty() -> None:
    """姓名长度 < 2 或 > 10 ⇒ 视为脏数据，跳过姓名扣分。"""
    text = "X 今天 X 讲解 X 示范"
    ratio = estimate_dominance_ratio(segment_text=text, target_coach_name="X")
    assert ratio >= 0.5  # 中性基线，未被扣
