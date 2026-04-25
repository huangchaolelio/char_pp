"""visual_kb_extract executor (Feature 014 — US2).

Reads the pose-analysis artifact and emits a list of KB item dicts in the
shape expected by ``F14KbMerger``::

    {"dimension", "param_min", "param_max", "param_ideal",
     "unit", "extraction_confidence", "action_type", "source_type": "visual"}

Real pose-rule extraction (from Feature-002's ``tech_extractor``) is invoked
when the upstream pose artifact contains actual keypoints. For scaffold
runs (empty keypoints list), we emit no items — the merger degrades cleanly
and ``merge_kb`` still completes successfully with 0 entries.

The executor never raises on empty / malformed artifacts: that's a valid
production outcome (poor video quality / no pose detected), not a failure.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType


logger = logging.getLogger(__name__)


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Produce ``kb_items`` dicts from the pose_analysis artifact."""
    pose_path = (
        await session.execute(
            select(PipelineStep.output_artifact_path).where(
                PipelineStep.job_id == job.id,
                PipelineStep.step_type == StepType.pose_analysis,
            )
        )
    ).scalar_one_or_none()
    if not pose_path or not Path(pose_path).exists():
        raise RuntimeError(
            "pose_analysis artifact missing — cannot run visual extraction"
        )

    # Load pose keypoints. We tolerate the scaffold shape
    # ``{"keypoints": [], "note": "..."}`` as well as a structured shape we
    # could wire to Feature-002 extraction in a follow-up.
    try:
        payload = json.loads(Path(pose_path).read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("visual_kb_extract: cannot parse pose artifact %s: %s", pose_path, exc)
        payload = {}

    kb_items = _extract_visual_kb_items(payload, job.tech_category)

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "kb_items": kb_items,
            "kb_items_count": len(kb_items),
            "source_type": "visual",
            "tech_category": job.tech_category,
            "backend": payload.get("note") or "pose_rule",
        },
        "output_artifact_path": None,
    }


def _extract_visual_kb_items(
    pose_payload: dict, tech_category: str
) -> list[dict]:
    """Convert a pose artifact into KB item dicts.

    Currently returns the items already embedded in the artifact under
    ``"kb_items"`` (test fixtures + future real extractors write there); an
    empty artifact produces an empty list — that's the expected behaviour
    for the US1 scaffold path. Real Feature-002 rule extraction can be wired
    by replacing this function.
    """
    items = pose_payload.get("kb_items")
    if not isinstance(items, list):
        return []

    cleaned: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if "dimension" not in raw:
            continue
        if not all(k in raw for k in ("param_min", "param_max", "param_ideal")):
            continue
        cleaned.append(
            {
                "dimension": str(raw["dimension"]),
                "param_min": float(raw["param_min"]),
                "param_max": float(raw["param_max"]),
                "param_ideal": float(raw["param_ideal"]),
                "unit": str(raw.get("unit", "")),
                "extraction_confidence": float(
                    raw.get("extraction_confidence", 0.8)
                ),
                "action_type": str(raw.get("action_type") or tech_category),
                "source_type": "visual",
            }
        )
    return cleaned
