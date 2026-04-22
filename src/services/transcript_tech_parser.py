"""TranscriptTechParser — extracts technical dimensions from Whisper transcription sentences.

Algorithm:
1. For each sentence, check if any BODY_PART_MAP keyword is present
2. If found, run numeric extraction regexes to find param ranges or single values
3. Sentences with numeric params → TechSemanticSegment (is_reference_note=False)
4. Sentences with body part but no numeric params → reference note (is_reference_note=True)
5. Sentences with neither → skipped (no segment created)
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from src.models.tech_semantic_segment import TechSemanticSegment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Body part → dimension mapping
# ---------------------------------------------------------------------------
# Each entry: (regex pattern, dimension name)
# Order matters: first match wins.
BODY_PART_MAP: list[tuple[str, str]] = [
    (r"肘(?:部|关节|弯)?", "elbow_angle"),
    (r"手腕|腕(?:部|关节)?", "wrist_angle"),
    (r"膝(?:盖|部|关节)?", "knee_angle"),
    (r"髋(?:部|关节)?", "hip_angle"),
    (r"肩(?:部|关节)?", "shoulder_angle"),
    (r"踝(?:关节|部)?|脚踝", "ankle_angle"),
    (r"重心", "weight_transfer"),
    (r"击球时机|击球时间|contact", "contact_timing"),
    (r"步频|步速", "footwork_frequency"),
    (r"挥拍速度|拍速", "swing_speed"),
]

# ---------------------------------------------------------------------------
# Numeric extraction regexes
# ---------------------------------------------------------------------------
# Pattern: range (min-max) with optional unit
_RANGE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*"     # min value
    r"(?:°|度|ms|秒|s|cm|mm|%)?"  # optional unit before dash
    r"\s*[-~到至]\s*"          # range separator
    r"(\d+(?:\.\d+)?)\s*"     # max value
    r"(°|度|ms|毫秒|秒|s|cm|mm|%)?",  # unit after max
    re.UNICODE,
)

# Pattern: single value (e.g., "保持90度", "约90°")
_SINGLE_PATTERN = re.compile(
    r"(?:保持|约|大约|达到|接近)?\s*"
    r"(\d+(?:\.\d+)?)\s*"
    r"(°|度|ms|毫秒|秒|s|cm|mm|%)",
    re.UNICODE,
)

# Normalize unit aliases
_UNIT_NORMALIZE = {
    "度": "°",
    "毫秒": "ms",
    "秒": "s",
}


def _normalize_unit(unit: Optional[str]) -> str:
    if not unit:
        return "°"  # default for angle measurements
    return _UNIT_NORMALIZE.get(unit, unit)


def _compute_parse_confidence(
    has_range: bool,
    body_part_match_quality: float,
    sentence_confidence: float,
) -> float:
    """Heuristic parse confidence score [0, 1]."""
    base = 0.5 if has_range else 0.35
    return min(1.0, base + 0.25 * body_part_match_quality + 0.25 * sentence_confidence)


class TranscriptTechParser:
    """Parses Whisper transcription sentences into TechSemanticSegment objects."""

    def parse(self, sentences: list[dict]) -> list[TechSemanticSegment]:
        """Parse a list of transcription sentences.

        Args:
            sentences: List of dicts from SpeechRecognizer: [{start, end, text, confidence}]

        Returns:
            List of TechSemanticSegment objects. Caller should filter
            is_reference_note=False to get KB-worthy segments.
        """
        results: list[TechSemanticSegment] = []

        for sent in sentences:
            text: str = sent.get("text", "").strip()
            start_s: float = float(sent.get("start", 0.0))
            end_s: float = float(sent.get("end", 0.0))
            sent_confidence: float = float(sent.get("confidence", 0.0))

            if not text:
                continue

            # Step 1: Find body part keyword
            dimension, bp_quality = self._find_dimension(text)

            if dimension is None:
                # No body part found — check if it's a generic tech reference
                # Skip silently (no segment needed)
                continue

            # Step 2: Try to extract numeric range
            seg = TechSemanticSegment()
            seg.start_ms = int(start_s * 1000)
            seg.end_ms = int(end_s * 1000)
            seg.source_sentence = text
            seg.dimension = dimension

            range_match = _RANGE_PATTERN.search(text)
            single_match = _SINGLE_PATTERN.search(text)

            if range_match:
                min_val = float(range_match.group(1))
                max_val = float(range_match.group(2))
                unit = _normalize_unit(range_match.group(3))

                seg.param_min = min_val
                seg.param_max = max_val
                seg.param_ideal = round((min_val + max_val) / 2, 2)
                seg.unit = unit
                seg.is_reference_note = False
                seg.parse_confidence = _compute_parse_confidence(True, bp_quality, sent_confidence)

            elif single_match:
                val = float(single_match.group(1))
                unit = _normalize_unit(single_match.group(2))

                seg.param_min = val
                seg.param_max = val
                seg.param_ideal = val
                seg.unit = unit
                seg.is_reference_note = False
                seg.parse_confidence = _compute_parse_confidence(False, bp_quality, sent_confidence)

            else:
                # Body part found but no numeric value → reference note
                seg.dimension = None  # reference notes have no quantified dimension
                seg.param_min = None
                seg.param_max = None
                seg.param_ideal = None
                seg.unit = None
                seg.is_reference_note = True
                seg.parse_confidence = 0.0

            results.append(seg)
            logger.debug(
                "Parsed sentence [%.1f-%.1fs]: dim=%s ref_note=%s confidence=%.2f",
                start_s, end_s, dimension, seg.is_reference_note, seg.parse_confidence,
            )

        quantified = sum(1 for r in results if not r.is_reference_note)
        reference = sum(1 for r in results if r.is_reference_note)
        logger.info(
            "TranscriptTechParser: %d sentences → %d segments (%d quantified, %d reference notes)",
            len(sentences), len(results), quantified, reference,
        )
        return results

    def _find_dimension(self, text: str) -> tuple[Optional[str], float]:
        """Find the first matching body part dimension in text.

        Returns:
            (dimension_name, match_quality) where match_quality is 1.0 for full
            keyword match, 0.7 for partial.
        """
        for pattern, dimension in BODY_PART_MAP:
            if re.search(pattern, text, re.UNICODE):
                return dimension, 1.0
        return None, 0.0
