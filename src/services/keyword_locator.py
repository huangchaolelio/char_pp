"""KeywordLocator — finds high-value demonstration segments in transcription sentences.

Algorithm:
1. Load a JSON keyword list (e.g. ["示范", "注意看", "标准动作", ...])
2. For each transcription sentence, scan for any keyword in the list
3. On first match: create a PriorityWindow centered on the sentence timestamps ± window_s
4. Clamp windows to [0, video_duration_ms]
5. Sort by start_ms and merge overlapping/adjacent windows

PriorityWindows are consumed by the action segmenter to prioritise frames inside
these windows for tech-point extraction (US2 — Feature 002).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PriorityWindow:
    """A time window identified as high-priority for tech-point extraction."""

    start_ms: int
    end_ms: int
    trigger_keyword: str

    def contains(self, ms: int) -> bool:
        """Return True when the given timestamp (ms) falls within this window."""
        return self.start_ms <= ms <= self.end_ms


class KeywordLocator:
    """Locates priority demonstration segments based on coaching keyword hits.

    Args:
        keyword_file_path: Path to a JSON file containing a list of keyword strings.
    """

    def __init__(self, keyword_file_path: str) -> None:
        path = Path(keyword_file_path)
        if not path.exists():
            raise FileNotFoundError(f"Keyword file not found: {keyword_file_path}")
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
        # Support both plain array and versioned object {keywords: [...]}
        if isinstance(raw, dict):
            if "keywords" not in raw:
                raise ValueError(f"Keyword file dict must have 'keywords' key, got {list(raw.keys())}")
            raw = raw["keywords"]
        if not isinstance(raw, list):
            raise ValueError(f"Keyword file must contain a JSON array or {{keywords: [...]}}, got {type(raw)}")
        self._keywords: list[str] = [str(k) for k in raw if k]
        logger.info("KeywordLocator loaded %d keywords from %s", len(self._keywords), keyword_file_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def locate(
        self,
        sentences: list[dict],
        video_duration_ms: int,
        window_s: float = 3.0,
    ) -> list[PriorityWindow]:
        """Scan sentences for keyword hits and return merged priority windows.

        Args:
            sentences: Whisper sentence dicts [{start, end, text, confidence}].
            video_duration_ms: Total video duration in milliseconds (upper clamp bound).
            window_s: Half-window size in seconds added before/after the sentence.

        Returns:
            Sorted list of non-overlapping PriorityWindow objects.
        """
        window_ms = int(window_s * 1000)
        raw_windows: list[PriorityWindow] = []

        for sent in sentences:
            text: str = sent.get("text", "").strip()
            if not text:
                continue

            keyword = self._first_hit(text)
            if keyword is None:
                continue

            start_s: float = float(sent.get("start", 0.0))
            end_s: float = float(sent.get("end", start_s))

            start_ms = max(0, int(start_s * 1000) - window_ms)
            end_ms = min(video_duration_ms, int(end_s * 1000) + window_ms)

            raw_windows.append(PriorityWindow(
                start_ms=start_ms,
                end_ms=end_ms,
                trigger_keyword=keyword,
            ))
            logger.debug(
                "Keyword hit '%s' at %.1f-%.1fs → window [%dms, %dms]",
                keyword, start_s, end_s, start_ms, end_ms,
            )

        merged = _merge_windows(raw_windows)
        logger.info(
            "KeywordLocator: %d sentences → %d keyword hits → %d merged windows",
            len(sentences), len(raw_windows), len(merged),
        )
        return merged

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _first_hit(self, text: str) -> Optional[str]:
        """Return the first keyword found in text, or None."""
        for kw in self._keywords:
            if kw in text:
                return kw
        return None


# ---------------------------------------------------------------------------
# Window merging utility
# ---------------------------------------------------------------------------

def _merge_windows(windows: list[PriorityWindow]) -> list[PriorityWindow]:
    """Sort and merge overlapping or adjacent PriorityWindows.

    When windows overlap, the merged window inherits the trigger_keyword of
    the earlier (lower start_ms) window.
    """
    if not windows:
        return []

    sorted_wins = sorted(windows, key=lambda w: w.start_ms)
    merged: list[PriorityWindow] = [sorted_wins[0]]

    for current in sorted_wins[1:]:
        last = merged[-1]
        if current.start_ms <= last.end_ms:
            # Overlap or adjacent — extend the last window
            merged[-1] = PriorityWindow(
                start_ms=last.start_ms,
                end_ms=max(last.end_ms, current.end_ms),
                trigger_keyword=last.trigger_keyword,
            )
        else:
            merged.append(current)

    return merged
