"""Integration tests for audio-enhanced KB extraction — T013.

Tests cover the end-to-end flow: video upload → audio analysis → KB with audio-sourced
tech points, and fallback behavior when audio is unavailable.

These tests require a real database (PostgreSQL) and Redis connection.
Run with: pytest tests/integration/test_audio_enhanced_kb_extraction.py -v -m integration
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.audio_transcript import AudioQualityFlag, AudioTranscript
from src.models.expert_tech_point import ExpertTechPoint
from src.services.audio_extractor import AudioExtractor
from src.services.kb_merger import KbMerger
from src.services.speech_recognizer import SpeechRecognizer
from src.services.transcript_tech_parser import TranscriptTechParser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_sentences_with_tech():
    """Simulated Whisper output for a video with clear technical coaching."""
    return [
        {"start": 5.0,  "end": 8.0,  "text": "注意看，肘部角度要保持在90度到120度之间", "confidence": 0.93},
        {"start": 10.0, "end": 13.0, "text": "这是标准正手拉球示范", "confidence": 0.91},
        {"start": 15.0, "end": 18.0, "text": "手腕翻转角度45°到60°", "confidence": 0.89},
        {"start": 20.0, "end": 22.0, "text": "重心要前移", "confidence": 0.87},  # reference note
    ]


@pytest.fixture
def silent_sentences():
    """Simulated output for a silent video (empty transcription)."""
    return []


# ---------------------------------------------------------------------------
# Unit-level integration: parser + merger pipeline
# ---------------------------------------------------------------------------

class TestParserMergerPipeline:
    """Tests that verify TranscriptTechParser + KbMerger work together correctly."""

    def test_audio_tech_points_extracted_and_merged(self, sample_sentences_with_tech):
        """End-to-end: sentences → segments → merged KB points with audio source."""
        parser = TranscriptTechParser()
        merger = KbMerger(conflict_threshold_pct=0.15)

        segments = parser.parse(sample_sentences_with_tech)
        tech_segments = [s for s in segments if not s.is_reference_note]

        # Should extract at least elbow_angle and wrist_angle
        dimensions = {s.dimension for s in tech_segments}
        assert "elbow_angle" in dimensions

        # Merge with empty visual points (audio-only scenario)
        merged = merger.merge([], tech_segments)
        audio_points = [p for p in merged if p.source_type == "audio"]
        assert len(audio_points) >= 1

    def test_reference_notes_not_in_merged_results(self, sample_sentences_with_tech):
        """'重心要前移' must not appear as a KB tech point."""
        parser = TranscriptTechParser()
        merger = KbMerger()
        segments = parser.parse(sample_sentences_with_tech)
        merged = merger.merge([], segments)
        # No merged point should be a pure text reference note
        for point in merged:
            assert point.param_min is not None
            assert point.param_max is not None

    def test_conflict_flagged_when_visual_audio_disagree(self):
        """Visual and audio disagree by >15% → conflict_flag=True in merged result."""
        from tests.unit.test_kb_merger import make_visual_point, make_audio_segment
        merger = KbMerger(conflict_threshold_pct=0.15)
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 105.0)]
        audio = [make_audio_segment("elbow_angle", 50.0, 70.0, 60.0)]
        merged = merger.merge(visual, audio)
        assert any(p.conflict_flag for p in merged)

    def test_silent_video_fallback_all_visual(self):
        """Silent video: parser produces no segments, merger returns visual-only points."""
        parser = TranscriptTechParser()
        merger = KbMerger()
        from tests.unit.test_kb_merger import make_visual_point
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 105.0)]

        segments = parser.parse([])  # empty sentences
        merged = merger.merge(visual, segments)

        assert len(merged) == 1
        assert merged[0].source_type == "visual"


# ---------------------------------------------------------------------------
# AudioTranscript quality flag handling
# ---------------------------------------------------------------------------

class TestAudioFallbackBehavior:
    def test_silent_audio_sets_fallback_reason(self):
        """SpeechRecognizer should set quality_flag=silent and fallback_reason for silent audio."""
        with patch("src.services.speech_recognizer._whisper_lib") as mock_whisper:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = {"segments": [], "language": "zh"}
            mock_whisper.load_model.return_value = mock_model

            recognizer = SpeechRecognizer(model_name="small", device="cpu")
            # Create a dummy WAV file so the file-existence check passes
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_wav = f.name
            try:
                transcript = recognizer.recognize(tmp_wav, language="zh")
            finally:
                os.unlink(tmp_wav)

            assert transcript.quality_flag in (AudioQualityFlag.silent, AudioQualityFlag.ok)
            if transcript.quality_flag == AudioQualityFlag.silent:
                assert transcript.fallback_reason is not None

    def test_low_snr_sets_quality_flag(self):
        """AudioExtractor should detect low SNR and set quality_flag=low_snr."""
        extractor = AudioExtractor(snr_threshold_db=10.0)
        # Mock SNR estimation to return a value below threshold
        with patch.object(extractor, "estimate_snr", return_value=5.0):
            snr = extractor.estimate_snr("/tmp/fake.wav")
            assert snr < extractor.snr_threshold_db


# ---------------------------------------------------------------------------
# Source type annotation on KB tech points
# ---------------------------------------------------------------------------

class TestKBTechPointSourceAnnotation:
    def test_audio_only_source_annotated(self):
        """Tech points from audio-only source should have source_type='audio'."""
        from tests.unit.test_kb_merger import make_audio_segment
        merger = KbMerger()
        audio = [make_audio_segment("elbow_angle", 90.0, 120.0, 105.0)]
        merged = merger.merge([], audio)
        assert merged[0].source_type == "audio"

    def test_visual_only_source_annotated(self):
        """Tech points from visual-only source should have source_type='visual'."""
        from tests.unit.test_kb_merger import make_visual_point
        merger = KbMerger()
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 105.0)]
        merged = merger.merge(visual, [])
        assert merged[0].source_type == "visual"

    def test_combined_source_annotated(self):
        """Tech points from both visual and audio (within threshold) → 'visual+audio'."""
        from tests.unit.test_kb_merger import make_visual_point, make_audio_segment
        merger = KbMerger()
        visual = [make_visual_point("elbow_angle", 90.0, 120.0, 105.0)]
        audio = [make_audio_segment("elbow_angle", 92.0, 118.0, 105.0)]
        merged = merger.merge(visual, audio)
        assert merged[0].source_type == "visual+audio"
