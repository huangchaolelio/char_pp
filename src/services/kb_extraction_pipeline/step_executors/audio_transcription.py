"""audio_transcription executor (Feature 014) — US1 scaffold.

Honours ``ExtractionJob.enable_audio_analysis``: when False, the step self-skips
(FR-012) and its output_summary surfaces the reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Stub: transcribe audio with Whisper.

    Short-circuits to ``skipped`` when ``enable_audio_analysis=False``.
    """
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

    video_path = (
        await session.execute(
            select(PipelineStep.output_artifact_path).where(
                PipelineStep.job_id == job.id,
                PipelineStep.step_type == StepType.download_video,
            )
        )
    ).scalar_one_or_none()
    if not video_path or not Path(video_path).exists():
        raise RuntimeError(
            "download_video artifact missing — cannot run audio transcription"
        )

    # Write a placeholder transcript artifact.
    out_path = Path(video_path).parent / "transcript.json"
    payload = {
        "video_path": video_path,
        "segments": [],
        "note": "scaffold_output_pending_feature014_us2_implementation",
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False))

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "whisper_model": "small",
            "language_detected": job.audio_language,
            "transcript_chars": 0,
            "skipped": False,
            "skip_reason": None,
        },
        "output_artifact_path": str(out_path),
    }
