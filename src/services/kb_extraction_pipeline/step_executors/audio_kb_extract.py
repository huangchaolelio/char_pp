"""audio_kb_extract executor (Feature 014 — US2).

Reads the transcript artifact and emits KB item dicts. Real LLM-based
extraction (Venus → OpenAI) is invoked when the upstream transcript has
actual text; scaffold/empty transcripts produce an empty list — the merger
and merge_kb handle that cleanly.

Upstream skipped → this step propagates skipped (FR-012 degradation path).
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
    """Emit ``kb_items`` dicts from the audio transcript."""
    upstream = (
        await session.execute(
            select(PipelineStep).where(
                PipelineStep.job_id == job.id,
                PipelineStep.step_type == StepType.audio_transcription,
            )
        )
    ).scalar_one()

    if upstream.status == PipelineStepStatus.skipped:
        # Audio path is off — propagate skipped so merge_kb degrades.
        return {
            "status": PipelineStepStatus.skipped,
            "output_summary": {
                "skipped": True,
                "skip_reason": "audio_transcription_skipped",
                "kb_items": [],
            },
            "output_artifact_path": None,
        }

    transcript_path = upstream.output_artifact_path
    payload: dict = {}
    if transcript_path and Path(transcript_path).exists():
        try:
            payload = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "audio_kb_extract: cannot parse transcript %s: %s", transcript_path, exc
            )

    kb_items = _extract_audio_kb_items(payload, job.tech_category)

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "kb_items": kb_items,
            "kb_items_count": len(kb_items),
            "source_type": "audio",
            "llm_model": payload.get("llm_model") or "scaffold",
        },
        "output_artifact_path": None,
    }


def _extract_audio_kb_items(
    transcript_payload: dict, tech_category: str
) -> list[dict]:
    """Convert a transcript artifact into KB item dicts.

    Two paths:
      1. Real LLM extraction (future US follow-up): wire ``LLMClient`` here,
         parse its structured JSON response. For this MVP implementation we
         read any items the artifact already embedded (tests use this).
      2. Scaffold / empty transcript: return []. merge_kb then degrades.
    """
    items = transcript_payload.get("kb_items")
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
                    raw.get("extraction_confidence", 0.7)
                ),
                "action_type": str(raw.get("action_type") or tech_category),
                "source_type": "audio",
            }
        )
    return cleaned
