"""Feature-021 T029 — curation_service 视频级摘要派生单测.

聚焦 :func:`_aggregate_summary` — spec FR-004 + FR-009 派生口径：
- accepted_duration_ratio = sum(accepted segs) / sum(all segs)
- low_quality = ratio < rubric.low_quality_ratio (default 0.3)
- short_video = total_duration_seconds < rubric.short_video_seconds (default 30)
- audio_unavailable 直接透传（与 transcript 是否为空有关）
- 各类计数（accepted/rejected/uncertain）覆盖
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.services.curation.curation_service import _aggregate_summary
from src.services.curation.decision_engine import DecisionResult
from src.services.curation.rubric_loader import load


@dataclass
class _FakeSeg:
    """模拟 VideoPreprocessingSegment 的最小字段."""
    segment_index: int
    start_ms: int
    end_ms: int


@pytest.fixture(scope="module")
def rubric():
    from src.services.curation import rubric_loader as rl
    rl.reset_cache()
    return load("v1")


def _seg(idx: int, start_s: int, end_s: int) -> _FakeSeg:
    return _FakeSeg(segment_index=idx, start_ms=start_s * 1000, end_ms=end_s * 1000)


def _decision(d: str) -> DecisionResult:
    return DecisionResult(
        decision=d, validity_score=0.5, rejection_reason=None,
        decision_source="rule", dim_breakdown={},
    )


def test_aggregate_balanced_mixed(rubric) -> None:
    segs = [_seg(i, i * 10, (i + 1) * 10) for i in range(5)]  # 5×10s = 50s
    decs = [
        _decision("accepted"),
        _decision("accepted"),
        _decision("rejected"),
        _decision("uncertain"),
        _decision("accepted"),
    ]
    summary = _aggregate_summary(segs, decs, rubric, audio_unavailable=False)
    assert summary["total_segment_count"] == 5
    assert summary["accepted_segment_count"] == 3
    assert summary["rejected_segment_count"] == 1
    assert summary["uncertain_segment_count"] == 1
    assert summary["accepted_duration_seconds"] == 30.0
    assert summary["total_duration_seconds"] == 50.0
    assert summary["accepted_duration_ratio"] == 0.6
    assert summary["low_quality"] is False
    assert summary["short_video"] is False
    assert summary["audio_unavailable"] is False


def test_aggregate_zero_accepted_marks_low_quality(rubric) -> None:
    """spec FR-009 双阈值上限：accepted_duration_ratio == 0 ⇒ low_quality=True."""
    segs = [_seg(0, 0, 100), _seg(1, 100, 200)]
    decs = [_decision("rejected"), _decision("uncertain")]
    summary = _aggregate_summary(segs, decs, rubric, audio_unavailable=False)
    assert summary["accepted_duration_ratio"] == 0.0
    assert summary["low_quality"] is True


def test_aggregate_below_threshold_marks_low_quality(rubric) -> None:
    """ratio in (0, 0.3) ⇒ low_quality=True（仍执行 KB 但落 warning，由路由层处理）。"""
    # 1×10s accepted, 4×10s rejected/uncertain → ratio = 0.2
    segs = [_seg(i, i * 10, (i + 1) * 10) for i in range(5)]
    decs = [
        _decision("accepted"),
        _decision("rejected"),
        _decision("rejected"),
        _decision("rejected"),
        _decision("uncertain"),
    ]
    summary = _aggregate_summary(segs, decs, rubric, audio_unavailable=False)
    assert summary["accepted_duration_ratio"] == 0.2
    assert summary["low_quality"] is True


def test_aggregate_above_threshold_not_low_quality(rubric) -> None:
    """ratio >= 0.3 ⇒ low_quality=False."""
    segs = [_seg(i, i * 10, (i + 1) * 10) for i in range(5)]
    decs = [_decision("accepted")] * 2 + [_decision("rejected")] * 3
    summary = _aggregate_summary(segs, decs, rubric, audio_unavailable=False)
    assert summary["accepted_duration_ratio"] == 0.4
    assert summary["low_quality"] is False


def test_aggregate_short_video_flag(rubric) -> None:
    """total_duration_seconds < 30 ⇒ short_video=True."""
    segs = [_seg(0, 0, 5), _seg(1, 5, 10)]  # 共 10s
    decs = [_decision("accepted"), _decision("accepted")]
    summary = _aggregate_summary(segs, decs, rubric, audio_unavailable=False)
    assert summary["short_video"] is True
    assert summary["total_duration_seconds"] == 10.0


def test_aggregate_passes_through_audio_unavailable(rubric) -> None:
    segs = [_seg(0, 0, 100)]
    decs = [_decision("accepted")]
    s_with = _aggregate_summary(segs, decs, rubric, audio_unavailable=True)
    s_without = _aggregate_summary(segs, decs, rubric, audio_unavailable=False)
    assert s_with["audio_unavailable"] is True
    assert s_without["audio_unavailable"] is False


def test_aggregate_zero_duration_segments(rubric) -> None:
    """总时长 0 ⇒ ratio=0.0 + low_quality=True；不应除零崩溃。"""
    segs = [_FakeSeg(0, 0, 0)]
    decs = [_decision("uncertain")]
    summary = _aggregate_summary(segs, decs, rubric, audio_unavailable=True)
    assert summary["accepted_duration_ratio"] == 0.0
    assert summary["total_duration_seconds"] == 0.0
    assert summary["low_quality"] is True
