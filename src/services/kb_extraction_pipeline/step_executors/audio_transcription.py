"""audio_transcription executor (Feature 016 US2) — consume preprocessed audio.

Post-US2 pipeline:
    1. Short-circuit on ``enable_audio_analysis=False`` → ``skipped``.
    2. Resolve the download directory produced by ``download_video`` (directory
       artifact path containing ``audio.wav`` if the upstream preprocessing
       job reported ``has_audio=true``).
    3. Missing ``audio.wav`` (has_audio=false case) → ``skipped`` with
       ``WHISPER_NO_AUDIO`` prefix (FR-008).
    4. Estimate SNR (optional — best-effort, failures are non-fatal).
    5. Run ``SpeechRecognizer.recognize`` inside ``asyncio.to_thread`` —
       **device is always forced to 'cpu'** regardless of settings, to avoid
       GPU OOM on the shared pod (Feature-016 decision).
    6. If the transcript is flagged ``silent`` → ``skipped``.
    7. Serialize the ``TranscriptResult`` via ``write_transcript_artifact``.
    8. Return ``output_summary`` with ``audio_source='cos_preprocessed'`` +
       ``whisper_device='cpu'`` to advertise the new data path.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.audio_transcript import AudioQualityFlag, AudioTranscript
from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.services import speech_recognizer as _speech_mod
from src.services.audio_extractor import AudioExtractor
from src.services.kb_extraction_pipeline.artifact_io import write_transcript_artifact
from src.services.speech_recognizer import TranscriptResult
from src.services.kb_extraction_pipeline.error_codes import (
    WHISPER_LOAD_FAILED,
    WHISPER_NO_AUDIO,
    format_error,
)


logger = logging.getLogger(__name__)


# ── Module-level helpers (monkeypatch-friendly) ──────────────────────────────


async def _get_download_dir(session: AsyncSession, job: ExtractionJob) -> Path:
    """Resolve the download-step artifact directory for ``job``.

    ``download_video`` (post-US2) emits a *directory* path whose contents
    include ``seg_NNNN.mp4`` segments and optionally ``audio.wav``.

    Raises
    ------
    RuntimeError
        If no successful ``download_video`` step is recorded for this job.
    """
    artifact_path = (
        await session.execute(
            select(PipelineStep.output_artifact_path).where(
                PipelineStep.job_id == job.id,
                PipelineStep.step_type == StepType.download_video,
            )
        )
    ).scalar_one_or_none()
    if not artifact_path:
        raise RuntimeError(
            "download_video artifact missing — cannot run audio transcription"
        )
    path = Path(artifact_path)
    # Back-compat: older rows stored the file path; accept and walk up.
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise RuntimeError(
            f"download_video artifact directory does not exist: {path}"
        )
    return path


def _estimate_snr_if_possible(audio_path: Path) -> float | None:
    """Best-effort SNR estimate; returns ``None`` on any failure.

    The preprocessing-supplied WAV is known-good (validated upstream), but we
    still guard against format quirks — audio analytics should never fail the
    transcription step.
    """
    try:
        return AudioExtractor().estimate_snr(audio_path)
    except Exception as exc:  # pragma: no cover — defensive only
        logger.warning("audio_transcription: SNR estimate failed: %s", exc)
        return None


async def _upsert_audio_transcript(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    transcript: TranscriptResult,
) -> None:
    """Persist the transcription result into ``audio_transcripts``.

    Feature-014/016 的 DAG 把转写工件落到 ``transcript.json``，但 Feature-005 的
    ``/api/v1/teaching-tips/tasks/{id}/extract-tips`` 依赖 ``audio_transcripts``
    表读取 sentences。若不回写 DB，老接口在 KB 提取完成后仍会报
    ``NO_AUDIO_TRANSCRIPT``。此函数在 success / silent 分支都会被调用，保持表与
    文件工件同步。

    ``audio_transcripts.task_id`` 当前没有 UNIQUE 约束，但业务上要求单行
    （teaching_tips 用 ``scalar_one_or_none`` 查询）。因此这里采用
    "DELETE existing → INSERT fresh" 的写法实现幂等替换，兼容 DAG 重跑。
    """
    quality_flag = transcript.quality_flag
    if not isinstance(quality_flag, AudioQualityFlag):
        quality_flag = AudioQualityFlag(str(quality_flag))

    await session.execute(
        delete(AudioTranscript).where(AudioTranscript.task_id == task_id)
    )

    row = AudioTranscript(
        task_id=task_id,
        language=transcript.language or "zh",
        model_version=transcript.model_version or "unknown",
        total_duration_s=transcript.total_duration_s,
        snr_db=transcript.snr_db,
        quality_flag=quality_flag,
        fallback_reason=transcript.fallback_reason,
        sentences=list(transcript.sentences or []),
    )
    session.add(row)
    await session.flush()


# ── Executor ────────────────────────────────────────────────────────────────


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Run Whisper transcription over the pre-downloaded audio.wav."""
    if not job.enable_audio_analysis:
        return {
            "status": PipelineStepStatus.skipped,
            "output_summary": {
                "skipped": True,
                "skip_reason": "disabled_by_request",
                "whisper_model": None,
                "audio_source": "cos_preprocessed",
                "whisper_device": "cpu",
            },
            "output_artifact_path": None,
        }

    settings = get_settings()

    download_dir = await _get_download_dir(session, job)
    audio_path = download_dir / "audio.wav"

    # ── Step 1: Missing audio.wav → skipped (has_audio=false upstream) ───
    if not audio_path.exists():
        logger.info(
            "audio_transcription: no audio.wav in %s — upstream has_audio=false; skipping",
            download_dir,
        )
        return {
            "status": PipelineStepStatus.skipped,
            "output_summary": {
                "skipped": True,
                "skip_reason": format_error(
                    WHISPER_NO_AUDIO, "preprocessing_has_audio_false"
                ),
                "whisper_model": None,
                "audio_source": "cos_preprocessed",
                "whisper_device": "cpu",
            },
            "output_artifact_path": None,
        }

    # ── Step 2: SNR (observability, best-effort) ─────────────────────────
    snr_db = await asyncio.to_thread(_estimate_snr_if_possible, audio_path)

    # ── Step 3: Whisper transcription — FORCE CPU (Feature-016 decision) ─
    recognizer = _speech_mod.SpeechRecognizer(
        model_name=settings.whisper_model,
        device="cpu",
    )
    try:
        transcript = await asyncio.to_thread(
            recognizer.recognize, str(audio_path), job.audio_language
        )
    except Exception as exc:  # Whisper model load or inference failure
        raise RuntimeError(format_error(WHISPER_LOAD_FAILED, str(exc))) from exc

    transcript.snr_db = snr_db

    # ── Step 4: Silence check ────────────────────────────────────────────
    if transcript.quality_flag == AudioQualityFlag.silent:
        logger.info(
            "audio_transcription: silent audio in %s → skipping", audio_path
        )
        # Persist the silent transcript so downstream teaching_tips can tell
        # "pipeline ran & found silence" apart from "pipeline never ran".
        await _upsert_audio_transcript(
            session, task_id=job.analysis_task_id, transcript=transcript
        )
        return {
            "status": PipelineStepStatus.skipped,
            "output_summary": {
                "skipped": True,
                "skip_reason": "silence_below_snr_threshold",
                "whisper_model": settings.whisper_model,
                "snr_db": snr_db,
                "audio_source": "cos_preprocessed",
                "whisper_device": "cpu",
            },
            "output_artifact_path": None,
        }

    # ── Step 5: Serialize transcript.json ────────────────────────────────
    transcript_path = download_dir / "transcript.json"
    await asyncio.to_thread(
        write_transcript_artifact,
        transcript_path,
        video_path=str(download_dir),  # directory — no single source file
        audio_path=str(audio_path),
        transcript_result=transcript,
    )

    # ── Step 5b: Persist into audio_transcripts (feature-005 consumer) ───
    # The legacy teaching_tips endpoint reads from this table; DAG must
    # keep it in sync with the transcript.json artifact.
    await _upsert_audio_transcript(
        session, task_id=job.analysis_task_id, transcript=transcript
    )

    # ── Step 6: Rich summary ─────────────────────────────────────────────
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
            "whisper_device": "cpu",
            "audio_source": "cos_preprocessed",
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
