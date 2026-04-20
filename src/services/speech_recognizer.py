"""SpeechRecognizer — wraps OpenAI Whisper for local inference.

Responsibilities:
- Lazy-load Whisper model on first use (avoids 2-3s startup in import)
- Transcribe a WAV file to a list of timestamped sentences
- Detect audio quality issues: silent, low SNR, unsupported language
- Return an AudioTranscript domain object (not ORM, pure data)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

try:
    import whisper as _whisper_lib  # type: ignore[import]
except ImportError:
    _whisper_lib = None  # type: ignore[assignment]

from src.models.audio_transcript import AudioQualityFlag

logger = logging.getLogger(__name__)

# Languages supported by this system (Mandarin primary)
SUPPORTED_LANGUAGES = {"zh", "yue"}  # yue = Cantonese (v2)


@dataclass
class TranscriptResult:
    """In-memory transcript result (not an ORM model).

    Converted to AudioTranscript ORM when persisting to DB.
    """
    language: str
    model_version: str
    total_duration_s: Optional[float]
    snr_db: Optional[float]
    quality_flag: AudioQualityFlag
    fallback_reason: Optional[str]
    # List of dicts: [{start, end, text, confidence}]
    sentences: list[dict] = field(default_factory=list)


class SpeechRecognizer:
    """Local Whisper-based speech recognizer.

    The model is lazily loaded on the first call to recognize() to avoid
    slow import times in tests and CLI tools that don't need recognition.
    """

    def __init__(self, model_name: str = "small", device: str = "auto") -> None:
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self._model = None  # lazy-loaded
        self._model_version = f"whisper-{model_name}-20231117"

    @staticmethod
    def _resolve_device(device: str) -> str:
        """Resolve 'auto' to 'cuda' if available, otherwise 'cpu'."""
        if device != "auto":
            return device
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _load_model(self):
        """Load Whisper model on first use."""
        if self._model is None:
            if _whisper_lib is None:
                raise RuntimeError(
                    "openai-whisper is not installed. "
                    "Run: pip install openai-whisper==20231117"
                )
            logger.info("Loading Whisper model '%s' on %s ...", self.model_name, self.device)
            self._model = _whisper_lib.load_model(self.model_name, device=self.device)
            logger.info("Whisper model loaded.")
        return self._model

    def recognize(self, wav_path: str, language: str = "zh") -> TranscriptResult:
        """Transcribe a WAV file and return a TranscriptResult.

        Args:
            wav_path: Path to a 16kHz mono WAV file.
            language: Expected language code (default: 'zh' for Mandarin).

        Returns:
            TranscriptResult with sentences and quality metadata.
        """
        import os

        # Check for unsupported language
        if language not in SUPPORTED_LANGUAGES:
            logger.warning("Unsupported audio language '%s' — falling back to visual mode.", language)
            return TranscriptResult(
                language=language,
                model_version=self._model_version,
                total_duration_s=None,
                snr_db=None,
                quality_flag=AudioQualityFlag.unsupported_language,
                fallback_reason=f"Language '{language}' is not supported; supported: {sorted(SUPPORTED_LANGUAGES)}",
                sentences=[],
            )

        # Check file exists
        if not os.path.exists(wav_path):
            return TranscriptResult(
                language=language,
                model_version=self._model_version,
                total_duration_s=None,
                snr_db=None,
                quality_flag=AudioQualityFlag.silent,
                fallback_reason="WAV file not found",
                sentences=[],
            )

        try:
            model = self._load_model()

            logger.info("Transcribing %s (language=%s)...", wav_path, language)
            result = model.transcribe(
                wav_path,
                language=language,
                word_timestamps=False,
                verbose=False,
            )
        except Exception as exc:
            logger.error("Whisper transcription failed for %s: %s", wav_path, exc)
            return TranscriptResult(
                language=language,
                model_version=self._model_version,
                total_duration_s=None,
                snr_db=None,
                quality_flag=AudioQualityFlag.silent,
                fallback_reason=f"Transcription failed: {type(exc).__name__}: {exc}",
                sentences=[],
            )

        raw_segments = result.get("segments", [])
        sentences = [
            {
                "start": float(seg.get("start", 0.0)),
                "end": float(seg.get("end", 0.0)),
                "text": seg.get("text", "").strip(),
                "confidence": float(seg.get("avg_logprob", -1.0) + 1.0),  # normalize logprob to [0,1]
            }
            for seg in raw_segments
            if seg.get("text", "").strip()
        ]

        # Detect silent audio (no segments at all)
        if not sentences:
            return TranscriptResult(
                language=language,
                model_version=self._model_version,
                total_duration_s=result.get("duration"),
                snr_db=None,
                quality_flag=AudioQualityFlag.silent,
                fallback_reason="No speech detected in audio (silent or unintelligible)",
                sentences=[],
            )

        total_duration = max((s["end"] for s in sentences), default=0.0)
        logger.info(
            "Transcription complete: %d sentences, %.1fs total",
            len(sentences),
            total_duration,
        )

        return TranscriptResult(
            language=language,
            model_version=self._model_version,
            total_duration_s=total_duration,
            snr_db=None,  # SNR is set separately by AudioExtractor
            quality_flag=AudioQualityFlag.ok,
            fallback_reason=None,
            sentences=sentences,
        )
