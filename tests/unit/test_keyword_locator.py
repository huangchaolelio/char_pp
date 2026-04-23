"""Unit tests for KeywordLocator — T023.

Tests verify:
- Single keyword hit → correct priority window (keyword_time ± window_s)
- Multiple keyword hits → overlapping windows merged
- No keyword hit → empty list
- Window boundaries clamped (start_ms >= 0, end_ms <= video_duration_ms)
Run with: pytest tests/unit/test_keyword_locator.py -v
"""

import json
import os
import tempfile

import pytest

from src.services.keyword_locator import KeywordLocator, PriorityWindow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def keyword_file(tmp_path):
    """Create a temp keyword JSON file."""
    keywords = ["示范", "注意看", "标准动作", "这一拍", "关键点", "击球瞬间"]
    kw_file = tmp_path / "keywords.json"
    kw_file.write_text(json.dumps(keywords, ensure_ascii=False))
    return str(kw_file)


@pytest.fixture
def locator(keyword_file):
    return KeywordLocator(keyword_file_path=keyword_file)


# ---------------------------------------------------------------------------
# Single keyword hit
# ---------------------------------------------------------------------------

class TestSingleKeywordHit:
    def test_hit_returns_window_around_sentence(self, locator):
        """Sentence with '示范' at 10s-11s → window [7s, 14s] (±3s)."""
        sentences = [
            {"start": 10.0, "end": 11.0, "text": "这是标准正手拉球示范", "confidence": 0.93}
        ]
        windows = locator.locate(sentences, video_duration_ms=60_000, window_s=3.0)
        assert len(windows) == 1
        w = windows[0]
        assert w.start_ms == pytest.approx(7_000)   # 10s - 3s
        assert w.end_ms == pytest.approx(14_000)    # 11s + 3s
        assert w.trigger_keyword == "示范"

    def test_hit_records_trigger_keyword(self, locator):
        """trigger_keyword matches the keyword that caused the hit."""
        sentences = [
            {"start": 5.0, "end": 6.0, "text": "注意看这个动作", "confidence": 0.9}
        ]
        windows = locator.locate(sentences, video_duration_ms=30_000)
        assert len(windows) == 1
        assert windows[0].trigger_keyword == "注意看"

    def test_window_clamped_at_video_start(self, locator):
        """Sentence at 1s − 3s window = [−2s, ...] → clamped to 0."""
        sentences = [
            {"start": 1.0, "end": 2.0, "text": "示范一下", "confidence": 0.9}
        ]
        windows = locator.locate(sentences, video_duration_ms=30_000, window_s=3.0)
        assert len(windows) == 1
        assert windows[0].start_ms == 0  # clamped

    def test_window_clamped_at_video_end(self, locator):
        """Sentence near end: end+3s > duration → clamped to duration_ms."""
        duration_ms = 20_000
        sentences = [
            {"start": 18.0, "end": 19.0, "text": "关键点在这里", "confidence": 0.9}
        ]
        windows = locator.locate(sentences, video_duration_ms=duration_ms, window_s=3.0)
        assert len(windows) == 1
        assert windows[0].end_ms == duration_ms  # clamped


# ---------------------------------------------------------------------------
# No keyword hit
# ---------------------------------------------------------------------------

class TestNoKeywordHit:
    def test_no_hit_returns_empty(self, locator):
        """Sentences with no keywords → empty window list."""
        sentences = [
            {"start": 0.0, "end": 2.0, "text": "大家好今天讲一下技术", "confidence": 0.85},
            {"start": 3.0, "end": 5.0, "text": "这个动作很重要", "confidence": 0.87},
        ]
        windows = locator.locate(sentences, video_duration_ms=60_000)
        assert windows == []

    def test_empty_sentences_returns_empty(self, locator):
        windows = locator.locate([], video_duration_ms=60_000)
        assert windows == []


# ---------------------------------------------------------------------------
# Multiple keywords / window merging
# ---------------------------------------------------------------------------

class TestWindowMerging:
    def test_non_overlapping_windows_kept_separate(self, locator):
        """Two hits far apart → two separate windows."""
        sentences = [
            {"start": 5.0,  "end": 6.0,  "text": "注意看手腕动作", "confidence": 0.9},
            {"start": 50.0, "end": 51.0, "text": "示范正手拉球", "confidence": 0.9},
        ]
        windows = locator.locate(sentences, video_duration_ms=120_000, window_s=3.0)
        assert len(windows) == 2

    def test_overlapping_windows_merged(self, locator):
        """Two hits close together → windows overlap → merged into one."""
        sentences = [
            {"start": 10.0, "end": 11.0, "text": "注意看这里", "confidence": 0.9},
            {"start": 13.0, "end": 14.0, "text": "示范一下", "confidence": 0.9},
        ]
        # window_s=3: [7,14] and [10,17] → overlapping → merged [7,17]
        windows = locator.locate(sentences, video_duration_ms=60_000, window_s=3.0)
        assert len(windows) == 1
        assert windows[0].start_ms == 7_000
        assert windows[0].end_ms == 17_000

    def test_adjacent_windows_merged(self, locator):
        """Windows that just touch (end_ms == next start_ms) are merged."""
        sentences = [
            {"start": 10.0, "end": 11.0, "text": "注意看", "confidence": 0.9},
            {"start": 14.0, "end": 15.0, "text": "示范", "confidence": 0.9},
        ]
        # [7000, 14000] and [11000, 18000] → overlap → merged
        windows = locator.locate(sentences, video_duration_ms=60_000, window_s=3.0)
        assert len(windows) == 1

    def test_windows_sorted_by_start(self, locator):
        """Returned windows must be sorted by start_ms ascending."""
        sentences = [
            {"start": 40.0, "end": 41.0, "text": "示范", "confidence": 0.9},
            {"start": 5.0,  "end": 6.0,  "text": "注意看", "confidence": 0.9},
        ]
        windows = locator.locate(sentences, video_duration_ms=120_000, window_s=3.0)
        assert len(windows) == 2
        assert windows[0].start_ms < windows[1].start_ms


# ---------------------------------------------------------------------------
# PriorityWindow dataclass
# ---------------------------------------------------------------------------

class TestPriorityWindowDataclass:
    def test_priority_window_fields(self):
        w = PriorityWindow(start_ms=1000, end_ms=5000, trigger_keyword="示范")
        assert w.start_ms == 1000
        assert w.end_ms == 5000
        assert w.trigger_keyword == "示范"

    def test_window_contains_ms(self):
        """PriorityWindow.contains(ms) returns True when ms is in [start_ms, end_ms]."""
        w = PriorityWindow(start_ms=5000, end_ms=10000, trigger_keyword="示范")
        assert w.contains(7500)
        assert w.contains(5000)
        assert w.contains(10000)
        assert not w.contains(4999)
        assert not w.contains(10001)
