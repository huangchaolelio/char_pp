"""pose_analysis executor (Feature 014) — US1 scaffold.

Produces an empty/minimal keypoints artifact so downstream steps can proceed.
Full YOLOv8/MediaPipe integration is deferred to US2 algorithm tasks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Stub: extract pose keypoints from the downloaded video.

    TODO (US2 algorithm task): wire up the real pose_estimator + batched
    inference loop. For now we write an empty JSON artifact so that
    ``visual_kb_extract`` has something to read on the next wave.
    """
    # Find the video artifact from the upstream download_video step.
    from sqlalchemy import select

    from src.models.pipeline_step import StepType

    artifact_row = (
        await session.execute(
            select(PipelineStep.output_artifact_path).where(
                PipelineStep.job_id == job.id,
                PipelineStep.step_type == StepType.download_video,
            )
        )
    ).scalar_one_or_none()

    if not artifact_row or not Path(artifact_row).exists():
        raise RuntimeError(
            "download_video artifact missing — cannot run pose analysis"
        )

    # Write a placeholder artifact (empty keypoints).
    out_path = Path(artifact_row).parent / "pose.json"
    payload = {
        "video_path": artifact_row,
        "keypoints": [],
        "note": "scaffold_output_pending_feature014_us2_implementation",
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False))

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "keypoints_frame_count": 0,
            "detected_segments": 0,
            "backend": "scaffold",
        },
        "output_artifact_path": str(out_path),
    }
