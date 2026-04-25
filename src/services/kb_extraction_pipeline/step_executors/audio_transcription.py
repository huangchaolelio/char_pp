"""audio_transcription executor (Feature 015) — real Whisper integration.

Pipeline:
    1. Short-circuit on ``enable_audio_analysis=False`` → ``skipped``.
    2. Resolve the downloaded video path from the upstream step.
    3. Extract a 16 kHz mono WAV via ``AudioExtractor.extract_wav``.
       - No audio stream → ``skipped`` (WHISPER_NO_AUDIO, FR-008).
       - Other ffmpeg errors → propagate as RuntimeError with
         ``WHISPER_LOAD_FAILED:`` prefix so the retry policy can retry.
    4. Run ``SpeechRecognizer.recognize`` inside ``asyncio.to_thread`` — the
       Whisper inference is CPU/GPU-bound and will block the event loop.
    5. If the transcript is flagged ``silent`` → ``skipped``
       (silence_below_snr_threshold, FR-008).
    6. Serialize the ``TranscriptResult`` via ``write_transcript_artifact``.
    7. Return rich ``output_summary`` exposing whisper model + language
       (FR-014).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.audio_transcript import AudioQualityFlag
from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.services import speech_recognizer as _speech_mod
from src.services.audio_extractor import AudioExtractionError, AudioExtractor
from src.services.kb_extraction_pipeline.artifact_io import write_transcript_artifact
from src.services.kb_extraction_pipeline.error_codes import (
    WHISPER_LOAD_FAILED,
    WHISPER_NO_AUDIO,
    format_error,
)


logger = logging.getLogger(__name__)


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Run real Whisper transcription over the downloaded video."""
    if not job.enable_audio_analysis:
        return {
            "status": PipelineStepStatus.skipped,
            "output_summary": {
                "skipped": True,
                "skip_reason": "disabled_by_request",
                "whisper_model": None,
            },
            "output_artifact_path": None,
        }

    video_path_str = (
        await session.execute(
            select(PipelineStep.output_artifact_path).where(
                PipelineStep.job_id == job.id,
                PipelineStep.step_type == StepType.download_video,
            )
        )
    ).scalar_one_or_none()
    if not video_path_str or not Path(video_path_str).exists():
        raise RuntimeError(
            "download_video artifact missing — cannot run audio transcription"
        )

    video_path = Path(video_path_str)
    job_dir = video_path.parent
    audio_path = job_dir / "audio.wav"

    settings = get_settings()

    # ── Step 1: Extract WAV (CPU/ffmpeg-bound) ────────────────────────────
    extractor = AudioExtractor()
    try:
        await asyncio.to_thread(extractor.extract_wav, video_path, audio_path)
    except AudioExtractionError as exc:
        msg = str(exc).lower()
        if "no audio" in msg or "no such" in msg:
            logger.info(
                "audio_transcription: no audio stream in %s → skipping", video_path
            )
            return {
                "status": PipelineStepStatus.skipped,
                "output_summary": {
                    "skipped": True,
                    "skip_reason": f"{WHISPER_NO_AUDIO}: no_audio_track",
                    "whisper_model": None,
                },
                "output_artifact_path": None,
            }
        # Other ffmpeg failures are transient-ish — bubble up so the
        # retry policy can decide. The error_codes module lists
        # WHISPER_LOAD_FAILED under I/O retry.
        raise RuntimeError(format_error(WHISPER_LOAD_FAILED, str(exc))) from exc

    # ── Step 2: Estimate SNR (optional observability) ─────────────────────
    snr_db = await asyncio.to_thread(extractor.estimate_snr, audio_path)

    # ── Step 3: Whisper transcription ─────────────────────────────────────
    recognizer = _speech_mod.SpeechRecognizer(
        model_name=settings.whisper_model,
        device=settings.whisper_device,
    )
    try:
        transcript = await asyncio.to_thread(
            recognizer.recognize, str(audio_path), job.audio_language
        )
    except Exception as exc:  # Whisper model load or inference failure
        raise RuntimeError(format_error(WHISPER_LOAD_FAILED, str(exc))) from exc

    # Populate SNR on the TranscriptResult so the artifact carries it forward.
    transcript.snr_db = snr_db

    # ── Step 4: Quality-based skipping (FR-008) ───────────────────────────
    if transcript.quality_flag == AudioQualityFlag.silent:
        logger.info(
            "audio_transcription: silent/unintelligible audio in %s → skipping",
            video_path,
        )
        return {
            "status": PipelineStepStatus.skipped,
            "output_summary": {
                "skipped": True,
                "skip_reason": "silence_below_snr_threshold",
                "whisper_model": settings.whisper_model,
                "snr_db": snr_db,
            },
            "output_artifact_path": None,
        }

    # ── Step 5: Serialize transcript.json ────────────────────────────────
    transcript_path = job_dir / "transcript.json"
    await asyncio.to_thread(
        write_transcript_artifact,
        transcript_path,
        video_path=str(video_path),
        audio_path=str(audio_path),
        transcript_result=transcript,
    )

    # ── Step 6: Return rich summary (FR-014) ─────────────────────────────
    transcript_chars = sum(len(s.get("text", "")) for s in transcript.sentences)
    quality_flag_value = (
        transcript.quality_flag.value
        if hasattr(transcript.quality_flag, "value")
        else str(transcript.quality_flag)
    )

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "whisper_model": settings.whisper_model,
            "language_detected": transcript.language,
            "transcript_chars": transcript_chars,
            "sentences_count": len(transcript.sentences),
            "snr_db": snr_db,
            "quality_flag": quality_flag_value,
            "skipped": False,
            "skip_reason": None,
        },
        "output_artifact_path": str(transcript_path),
    }
