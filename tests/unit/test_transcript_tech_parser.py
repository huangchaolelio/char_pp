"""Unit tests for TranscriptTechParser — T011.

Tests verify numeric range extraction, single-value extraction,
pure-text sentences, and body-part dimension mapping.
Run with: pytest tests/unit/test_transcript_tech_parser.py -v
"""

import pytest

from src.services.transcript_tech_parser import TranscriptTechParser


@pytest.fixture
def parser():
    return TranscriptTechParser()


class TestNumericRangeExtraction:
    def test_degree_range_dash(self, parser):
        """'肘部角度90°-120°' → min=90, max=120, unit='°'"""
        sentences = [{"start": 1.0, "end": 3.0, "text": "肘部角度应保持在90°-120°之间", "confidence": 0.95}]
        results = parser.parse(sentences)
        assert len(results) == 1
        seg = results[0]
        assert seg.dimension == "elbow_angle"
        assert seg.param_min == pytest.approx(90.0)
        assert seg.param_max == pytest.approx(120.0)
        assert seg.unit == "°"
        assert seg.is_reference_note is False

    def test_degree_range_to_word(self, parser):
        """'90度到120度' → min=90, max=120"""
        sentences = [{"start": 0.0, "end": 2.0, "text": "肘部弯曲从90度到120度", "confidence": 0.9}]
        results = parser.parse(sentences)
        assert len(results) == 1
        assert results[0].param_min == pytest.approx(90.0)
        assert results[0].param_max == pytest.approx(120.0)

    def test_millisecond_range(self, parser):
        """'击球时机200ms-350ms' → min=200, max=350, unit='ms'"""
        sentences = [{"start": 5.0, "end": 7.0, "text": "击球时机控制在200ms到350ms", "confidence": 0.88}]
        results = parser.parse(sentences)
        assert len(results) == 1
        assert results[0].unit == "ms"
        assert results[0].param_min == pytest.approx(200.0)
        assert results[0].param_max == pytest.approx(350.0)


class TestSingleValueExtraction:
    def test_single_degree_ideal(self, parser):
        """'保持90度' → param_ideal=90, param_min=param_max=90"""
        sentences = [{"start": 0.0, "end": 2.0, "text": "膝盖弯曲保持90度", "confidence": 0.92}]
        results = parser.parse(sentences)
        assert len(results) == 1
        seg = results[0]
        assert seg.param_ideal == pytest.approx(90.0)
        assert seg.param_min == pytest.approx(90.0)
        assert seg.param_max == pytest.approx(90.0)

    def test_dimension_mapped_correctly_knee(self, parser):
        """膝盖/膝部 → 'knee_angle'"""
        sentences = [{"start": 0.0, "end": 2.0, "text": "膝盖弯曲保持90度", "confidence": 0.9}]
        results = parser.parse(sentences)
        assert results[0].dimension == "knee_angle"


class TestReferenceNoteHandling:
    def test_pure_text_returns_reference_note(self, parser):
        """'重心要前移' → is_reference_note=True, dimension=None"""
        sentences = [{"start": 0.0, "end": 1.5, "text": "重心要前移", "confidence": 0.9}]
        results = parser.parse(sentences)
        # Either empty result or reference note, never a KB tech point
        for seg in results:
            assert seg.is_reference_note is True
            assert seg.dimension is None

    def test_no_body_part_keyword_returns_empty_or_reference(self, parser):
        """Sentence with no body part keyword produces no KB-worthy segment."""
        sentences = [{"start": 0.0, "end": 1.0, "text": "这个动作很重要", "confidence": 0.85}]
        results = parser.parse(sentences)
        for seg in results:
            assert seg.is_reference_note is True

    def test_numeric_without_body_part_returns_reference(self, parser):
        """Number without body part context → reference note."""
        sentences = [{"start": 0.0, "end": 1.0, "text": "练习三次", "confidence": 0.9}]
        results = parser.parse(sentences)
        for seg in results:
            assert seg.is_reference_note is True


class TestBodyPartMapping:
    def test_elbow_variants(self, parser):
        """肘部/肘关节/肘 → elbow_angle"""
        for text in ["肘部角度保持90度", "肘关节弯曲90度", "肘弯90度"]:
            sentences = [{"start": 0.0, "end": 2.0, "text": text, "confidence": 0.9}]
            results = [s for s in parser.parse(sentences) if not s.is_reference_note]
            assert len(results) >= 1, f"Expected tech point for: {text}"
            assert results[0].dimension == "elbow_angle"

    def test_wrist_variants(self, parser):
        """腕部/手腕 → wrist_angle"""
        sentences = [{"start": 0.0, "end": 2.0, "text": "手腕翻转角度45°到60°", "confidence": 0.9}]
        results = [s for s in parser.parse(sentences) if not s.is_reference_note]
        assert len(results) >= 1
        assert results[0].dimension == "wrist_angle"

    def test_knee_variants(self, parser):
        """膝盖/膝部/膝关节 → knee_angle"""
        sentences = [{"start": 0.0, "end": 2.0, "text": "膝关节弯曲保持120度到140度", "confidence": 0.9}]
        results = [s for s in parser.parse(sentences) if not s.is_reference_note]
        assert len(results) >= 1
        assert results[0].dimension == "knee_angle"


class TestParseConfidence:
    def test_high_confidence_range_extraction(self, parser):
        """Range with explicit unit and clear body part → high parse_confidence"""
        sentences = [{"start": 0.0, "end": 2.0, "text": "肘部角度90°-120°", "confidence": 0.95}]
        results = [s for s in parser.parse(sentences) if not s.is_reference_note]
        assert results[0].parse_confidence >= 0.7

    def test_ambiguous_extraction_lower_confidence(self, parser):
        """Single value without range → lower confidence than range"""
        sentences_range = [{"start": 0.0, "end": 2.0, "text": "肘部角度90°-120°", "confidence": 0.9}]
        sentences_single = [{"start": 0.0, "end": 2.0, "text": "肘部保持90度", "confidence": 0.9}]
        results_range = [s for s in parser.parse(sentences_range) if not s.is_reference_note]
        results_single = [s for s in parser.parse(sentences_single) if not s.is_reference_note]
        if results_range and results_single:
            assert results_range[0].parse_confidence >= results_single[0].parse_confidence
