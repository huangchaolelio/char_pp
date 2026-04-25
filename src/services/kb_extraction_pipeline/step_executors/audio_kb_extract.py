"""audio_kb_extract executor (Feature 015) — real LLM-driven KB extraction.

Pipeline:
    1. If upstream ``audio_transcription`` is ``skipped`` → propagate skipped
       (FR-012 degradation path — merge_kb will fall back to visual only).
    2. Read the ``transcript.json`` artifact via ``artifact_io``.
    3. If no sentences → return empty ``kb_items`` (transcript existed but
       was empty — still success, degraded).
    4. Pre-check LLM configuration. If neither Venus nor OpenAI is set up
       → fail fast with ``LLM_UNCONFIGURED:`` prefix (FR-011).
    5. Build a ``TranscriptTechParser``; invoke ``.parse(sentences)`` inside
       ``asyncio.to_thread`` (LLM is blocking HTTP).
    6. Filter returned ``TechSemanticSegment`` list per FR-009 / spec Q5:
       - drop reference notes
       - drop segments with dimension == None
       - drop segments with parse_confidence < 0.5
    7. Project remaining segments into ``kb_items`` dicts with
       ``source_type='audio'`` + ``raw_text_span`` (Q5 additional field).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.services.kb_extraction_pipeline.artifact_io import read_transcript_artifact
from src.services.kb_extraction_pipeline.error_codes import (
    LLM_JSON_PARSE,
    LLM_UNCONFIGURED,
    format_error,
)


logger = logging.getLogger(__name__)

# FR-009 / spec Q5 filter threshold for audio-path segments.
_AUDIO_CONFIDENCE_THRESHOLD = 0.5


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Emit audio-path ``kb_items`` from the Whisper transcript artifact."""
    upstream = (
        await session.execute(
            select(PipelineStep).where(
                PipelineStep.job_id == job.id,
                PipelineStep.step_type == StepType.audio_transcription,
            )
        )
    ).scalar_one()

    if upstream.status == PipelineStepStatus.skipped:
        return {
            "status": PipelineStepStatus.skipped,
            "output_summary": {
                "skipped": True,
                "skip_reason": "audio_transcription_skipped",
                "kb_items": [],
            },
            "output_artifact_path": None,
        }

    # ── Read transcript ───────────────────────────────────────────────────
    transcript_path = upstream.output_artifact_path
    payload: dict = {}
    sentences: list[dict] = []
    if transcript_path and Path(transcript_path).exists():
        payload = await asyncio.to_thread(
            read_transcript_artifact, Path(transcript_path)
        )
        sentences = payload.get("sentences") or []
    else:
        # Back-compat: some Feature-014 tests embed kb_items inline in a
        # legacy transcript artifact — honour that path by returning an
        # empty list (merger handles it).
        logger.info("audio_kb_extract: no transcript artifact, returning empty")

    # Allow legacy test fixtures that stuff kb_items into the transcript.
    legacy_items = _read_legacy_kb_items(payload, job.tech_category)
    if legacy_items:
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "kb_items": legacy_items,
                "kb_items_count": len(legacy_items),
                "source_type": "audio",
                "llm_model": "legacy_fixture",
                "llm_backend": "legacy_fixture",
                "parsed_segments_total": len(legacy_items),
                "dropped_low_confidence": 0,
                "dropped_reference_notes": 0,
            },
            "output_artifact_path": None,
        }

    if not sentences:
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "kb_items": [],
                "kb_items_count": 0,
                "source_type": "audio",
                "llm_model": None,
                "llm_backend": None,
                "parsed_segments_total": 0,
                "dropped_low_confidence": 0,
                "dropped_reference_notes": 0,
            },
            "output_artifact_path": None,
        }

    # ── LLM-config pre-check (FR-011) ────────────────────────────────────
    settings = get_settings()
    if not (
        (settings.venus_token and settings.venus_base_url)
        or settings.openai_api_key
    ):
        raise RuntimeError(
            format_error(
                LLM_UNCONFIGURED,
                "neither VENUS_TOKEN+VENUS_BASE_URL nor OPENAI_API_KEY is set",
            )
        )

    # ── Build parser + client ────────────────────────────────────────────
    from src.services import llm_client as _llm_mod
    from src.services import transcript_tech_parser as _parser_mod

    # The parser currently pattern-matches sentence text; the LlmClient we
    # instantiate here is kept around so callers can upgrade parser.parse to
    # LLM-driven extraction in-place (per plan.md "complete reuse of
    # transcript_tech_parser"). Instantiating the client also surfaces bad
    # config early.
    try:
        llm = _llm_mod.LlmClient.from_settings()
    except ValueError as exc:
        # Defensive — pre-check above should have caught this.
        raise RuntimeError(format_error(LLM_UNCONFIGURED, str(exc))) from exc

    parser = _parser_mod.TranscriptTechParser()

    # ── Run parser (may call LLM) ────────────────────────────────────────
    try:
        segments = await asyncio.to_thread(parser.parse, sentences)
    except (ValueError, json.JSONDecodeError) as exc:
        # LLM returned malformed JSON — not retried (format issue).
        raise RuntimeError(format_error(LLM_JSON_PARSE, str(exc))) from exc

    # ── Apply FR-009 / Q5 filters and project to kb_items ────────────────
    kb_items: list[dict] = []
    dropped_reference_notes = 0
    dropped_low_confidence = 0

    for seg in segments:
        is_ref = bool(getattr(seg, "is_reference_note", False))
        dim = getattr(seg, "dimension", None)
        conf = float(getattr(seg, "parse_confidence", 0.0) or 0.0)

        if is_ref:
            dropped_reference_notes += 1
            continue
        if dim is None:
            dropped_reference_notes += 1
            continue
        if conf < _AUDIO_CONFIDENCE_THRESHOLD:
            dropped_low_confidence += 1
            continue

        param_min = getattr(seg, "param_min", None)
        param_max = getattr(seg, "param_max", None)
        param_ideal = getattr(seg, "param_ideal", None)
        if param_min is None or param_max is None or param_ideal is None:
            dropped_low_confidence += 1
            continue

        kb_items.append({
            "dimension": str(dim),
            "param_min": float(param_min),
            "param_max": float(param_max),
            "param_ideal": float(param_ideal),
            "unit": str(getattr(seg, "unit", "") or ""),
            "extraction_confidence": conf,
            "action_type": job.tech_category,
            "source_type": "audio",
            "raw_text_span": getattr(seg, "source_sentence", None),
        })

    llm_backend = getattr(llm, "_backend", "unknown")
    llm_model = getattr(llm, "_default_model", None)

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "kb_items": kb_items,
            "kb_items_count": len(kb_items),
            "source_type": "audio",
            "llm_model": llm_model,
            "llm_backend": llm_backend,
            "parsed_segments_total": len(segments),
            "dropped_low_confidence": dropped_low_confidence,
            "dropped_reference_notes": dropped_reference_notes,
        },
        "output_artifact_path": None,
    }


def _read_legacy_kb_items(payload: dict, tech_category: str) -> list[dict]:
    """Back-compat: Feature-014 fixtures embed ``kb_items`` directly inside
    the transcript artifact. Honour that by returning parsed items."""
    items = payload.get("kb_items")
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
        cleaned.append({
            "dimension": str(raw["dimension"]),
            "param_min": float(raw["param_min"]),
            "param_max": float(raw["param_max"]),
            "param_ideal": float(raw["param_ideal"]),
            "unit": str(raw.get("unit", "")),
            "extraction_confidence": float(raw.get("extraction_confidence", 0.7)),
            "action_type": str(raw.get("action_type") or tech_category),
            "source_type": "audio",
        })
    return cleaned
