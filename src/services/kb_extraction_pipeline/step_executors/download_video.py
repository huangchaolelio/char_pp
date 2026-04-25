"""download_video executor (Feature 014) — US1 scaffold.

Minimal scaffold that downloads the video from COS to a per-job local dir.
The full algorithm (progress tracking, resume, etc.) lives in later tasks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Download the COS video to a local job-scoped directory.

    Returns the orchestrator-expected dict:
      {status, output_summary, output_artifact_path}
    """
    settings = get_settings()
    job_dir = Path(settings.extraction_artifact_root) / str(job.id)
    job_dir.mkdir(parents=True, exist_ok=True)
    local_path = job_dir / "video.mp4"

    # Reuse already-downloaded file on rerun (US4 anticipation — safe no-op on first run).
    if step.output_artifact_path and Path(step.output_artifact_path).exists():
        size = Path(step.output_artifact_path).stat().st_size
        if size > 0:
            return {
                "status": PipelineStepStatus.success,
                "output_summary": {
                    "video_size_bytes": size,
                    "reused": True,
                },
                "output_artifact_path": step.output_artifact_path,
            }

    import asyncio

    from src.services import cos_client as cos_mod

    def _sync_download() -> int:
        client, bucket = cos_mod._get_cos_client()
        response = client.get_object(Bucket=bucket, Key=job.cos_object_key)
        response["Body"].get_stream_to_file(str(local_path))
        return local_path.stat().st_size

    size = await asyncio.to_thread(_sync_download)
    if size == 0:
        raise RuntimeError(
            f"downloaded file is zero bytes: {job.cos_object_key}"
        )

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "video_size_bytes": size,
            "reused": False,
        },
        "output_artifact_path": str(local_path),
    }
