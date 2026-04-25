"""merge_kb executor (Feature 014 — US2 real implementation).

Responsibilities (FR-011):
  1. Read visual + audio KB item lists from upstream step output_summary.
  2. Run ``F14KbMerger`` to split into (merged, conflicts).
  3. Persist merged items to ``expert_tech_points`` (creating a fresh draft
     ``tech_knowledge_bases`` row scoped to this job).
  4. Persist conflicts to ``kb_conflicts`` for human review.
  5. Handle degradation: when ``audio_kb_extract`` is skipped/failed, merge
     visual items only (FR-012).
  6. Flip ``coach_video_classifications.kb_extracted=True``.

The actual value mapping from the upstream ``output_summary.kb_items`` lists
to the merger's input shape is intentionally permissive — extractors can
surface dicts with minor schema drift (missing ``unit``, etc.), and we coerce
on the way in so a single bad item never nukes the merge.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.coach_video_classification import CoachVideoClassification
from src.models.expert_tech_point import ActionType, ExpertTechPoint
from src.models.extraction_job import ExtractionJob
from src.models.kb_conflict import KbConflict
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase
from src.services.kb_extraction_pipeline.merger import (
    ConflictItem,
    F14KbMerger,
    MergedPoint,
)

logger = logging.getLogger(__name__)


# Default action type for merged points that don't carry one — we never
# actually write with this because visual extraction always assigns a type.
_FALLBACK_ACTION_TYPE = "forehand_general"


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Merge the two KB item streams and commit to the database."""
    visual_step, audio_step = await _load_upstream_steps(session, job.id)

    if visual_step.status != PipelineStepStatus.success:
        # Visual is the hard requirement — the orchestrator should have
        # routed us away in that case, but double-check defensively.
        raise RuntimeError("visual_kb_extract did not succeed — merge_kb cannot run")

    visual_items = _safe_items(visual_step.output_summary)
    audio_items: list[dict] = []
    degraded = False
    if audio_step.status == PipelineStepStatus.success:
        audio_items = _safe_items(audio_step.output_summary)
    else:
        degraded = True

    merger = F14KbMerger()
    merged, conflicts = merger.merge(visual_items, audio_items)

    kb_version = await _ensure_kb_version(session, job, merged)
    inserted = await _persist_merged_points(
        session, job, kb_version, merged
    )
    await _persist_conflicts(session, job, conflicts)

    # Flip kb_extracted on the classification row — this is the visible
    # side-effect that satisfies Feature-013's ``kb_extracted`` consumers.
    await session.execute(
        update(CoachVideoClassification)
        .where(CoachVideoClassification.cos_object_key == job.cos_object_key)
        .values(kb_extracted=True, updated_at=datetime.now(timezone.utc))
    )
    await session.commit()

    summary = {
        "merged_items": len(merged),
        "inserted_tech_points": inserted,
        "conflict_items": len(conflicts),
        "degraded_mode": degraded,
        "kb_version": kb_version,
        "kb_extracted_flag_set": True,
    }
    logger.info(
        "merge_kb job=%s: merged=%d inserted=%d conflicts=%d degraded=%s",
        job.id, len(merged), inserted, len(conflicts), degraded,
    )
    return {
        "status": PipelineStepStatus.success,
        "output_summary": summary,
        "output_artifact_path": None,
    }


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _load_upstream_steps(
    session: AsyncSession, job_id
) -> tuple[PipelineStep, PipelineStep]:
    rows = (
        await session.execute(
            select(PipelineStep).where(
                PipelineStep.job_id == job_id,
                PipelineStep.step_type.in_(
                    [StepType.visual_kb_extract, StepType.audio_kb_extract]
                ),
            )
        )
    ).scalars().all()
    by_type = {r.step_type: r for r in rows}
    return by_type[StepType.visual_kb_extract], by_type[StepType.audio_kb_extract]


def _safe_items(output_summary: dict | None) -> list[dict]:
    """Extract the ``kb_items`` list from an upstream step's output_summary.

    Accepts missing/malformed payloads without crashing — returns [].
    """
    if not output_summary:
        return []
    items = output_summary.get("kb_items")
    if not isinstance(items, list):
        return []
    # Drop entries missing the required keys to keep the merger pure.
    cleaned: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if "dimension" not in raw:
            continue
        if not all(k in raw for k in ("param_min", "param_max", "param_ideal")):
            continue
        cleaned.append(raw)
    return cleaned


async def _ensure_kb_version(
    session: AsyncSession,
    job: ExtractionJob,
    merged: list[MergedPoint],
) -> str:
    """Resolve (or create) the ``tech_knowledge_bases`` row for this job.

    We derive a version string ``0.{a}.{b}`` deterministically from the job UUID
    so reruns pick up the same version and can rely on the upstream UPSERT.
    """
    version = _version_from_job_id(job.id)

    existing = (
        await session.execute(
            select(TechKnowledgeBase).where(TechKnowledgeBase.version == version)
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            TechKnowledgeBase(
                version=version,
                action_types_covered=_derive_action_types(merged, job.tech_category),
                point_count=0,          # updated after insert
                status=KBStatus.draft,
                notes=(
                    f"F-014 draft KB for extraction_job={job.id} "
                    f"cos_key={job.cos_object_key}"
                ),
            )
        )
        await session.flush()
    return version


def _version_from_job_id(job_id) -> str:
    """Derive a stable semver-compatible version string from a UUID.

    The ``tech_knowledge_bases.version`` column is ``VARCHAR(20)``, so we
    keep each numeric segment short: two 16-bit chunks from the UUID give
    a max string of ``"0.65535.65535"`` = 13 chars, well within budget.

    Collisions are theoretically possible at 2^32 range but the KB-extraction
    channel's concurrency is 2 and daily job volume is tiny (~5/day per
    spec assumptions), so the birthday-bound is negligible for this use case.
    """
    raw = job_id.hex if hasattr(job_id, "hex") else str(job_id).replace("-", "")
    a = int(raw[:4], 16)
    b = int(raw[4:8], 16)
    return f"0.{a}.{b}"


def _derive_action_types(
    merged: list[MergedPoint], fallback: str
) -> list[str]:
    types: set[str] = set()
    for m in merged:
        if m.action_type:
            types.add(m.action_type)
    # If visual produced nothing that carries an action_type, fall back to the
    # job's classified tech_category so the column is not empty.
    if not types:
        types.add(fallback or _FALLBACK_ACTION_TYPE)
    return sorted(types)


async def _persist_merged_points(
    session: AsyncSession,
    job: ExtractionJob,
    kb_version: str,
    merged: list[MergedPoint],
) -> int:
    """INSERT one ExpertTechPoint row per merged item. Returns inserted count.

    Silently skips points whose ``action_type`` can't be mapped to the
    ``ActionType`` enum (e.g. the job's tech_category is one of the F-014 21
    categories that isn't in the F-002 action enum). We log each skip so
    operators can see which dimensions got dropped.
    """
    if not merged:
        return 0

    inserted = 0
    for m in merged:
        action_enum = _coerce_action_type(m.action_type, job.tech_category)
        if action_enum is None:
            logger.info(
                "merge_kb skip: dimension=%s has no ActionType mapping "
                "(got action_type=%r, tech_category=%r)",
                m.dimension, m.action_type, job.tech_category,
            )
            continue

        # The CheckConstraint requires param_min <= param_ideal <= param_max.
        # Clamp softly in case the merger produced out-of-order bounds (edge
        # case when visual + audio have wildly different ranges but ideal
        # still falls within 10%).
        p_min = min(m.param_min, m.param_ideal, m.param_max)
        p_max = max(m.param_min, m.param_ideal, m.param_max)
        p_ideal = max(p_min, min(m.param_ideal, p_max))

        session.add(
            ExpertTechPoint(
                knowledge_base_version=kb_version,
                action_type=action_enum,
                dimension=m.dimension,
                param_min=p_min,
                param_max=p_max,
                param_ideal=p_ideal,
                unit=m.unit or "",
                extraction_confidence=max(0.0, min(1.0, m.extraction_confidence)),
                source_video_id=job.analysis_task_id,
                source_type=m.source_type,
                conflict_flag=False,  # F-014 conflicts go to kb_conflicts
                conflict_detail=None,
            )
        )
        inserted += 1

    # Bump point_count on the KB row.
    if inserted:
        await session.execute(
            update(TechKnowledgeBase)
            .where(TechKnowledgeBase.version == kb_version)
            .values(point_count=TechKnowledgeBase.point_count + inserted)
        )
    return inserted


def _coerce_action_type(
    raw: str | None, fallback: str | None
) -> ActionType | None:
    """Map a free-form action type string to the ``ActionType`` enum.

    - Direct match on ActionType value → use it.
    - Otherwise try the job's tech_category.
    - Otherwise None (caller logs & skips).
    """
    for candidate in (raw, fallback):
        if not candidate:
            continue
        try:
            return ActionType(candidate)
        except ValueError:
            continue
    return None


async def _persist_conflicts(
    session: AsyncSession,
    job: ExtractionJob,
    conflicts: list[ConflictItem],
) -> None:
    if not conflicts:
        return
    for c in conflicts:
        session.add(
            KbConflict(
                job_id=job.id,
                cos_object_key=job.cos_object_key,
                tech_category=c.tech_category or job.tech_category,
                dimension_name=c.dimension_name,
                visual_value=c.visual_value,
                audio_value=c.audio_value,
                visual_confidence=c.visual_confidence,
                audio_confidence=c.audio_confidence,
            )
        )
