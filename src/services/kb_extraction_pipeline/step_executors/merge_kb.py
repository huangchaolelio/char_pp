"""merge_kb executor (Feature 014 — US2 real implementation; Feature-019 adapted).

Responsibilities (FR-011):
  1. Read visual + audio KB item lists from upstream step output_summary.
  2. Run ``F14KbMerger`` to split into (merged, conflicts).
  3. Persist merged items to ``expert_tech_points`` (creating a fresh draft
     ``tech_knowledge_bases`` row scoped to this job).
  4. Persist conflicts to ``kb_conflicts`` for human review.
  5. Handle degradation: when ``audio_kb_extract`` is skipped/failed, merge
     visual items only (FR-012).
  6. Flip ``coach_video_classifications.kb_extracted=True``.

Feature-019 变更:
  - tech_knowledge_bases 使用复合主键 ``(tech_category, version INTEGER)``
  - version 由 `knowledge_base_svc.create_draft_version` 计算（per-category MAX+1）
  - 每条 extraction_job 产出恰好 1 条 KB（绑 job.tech_category）；若 merged 点中
    出现跨类别（理论不应，上游已对齐）仅 warning 不分裂
  - ExpertTechPoint 用复合 FK (kb_tech_category, kb_version)
"""

from __future__ import annotations

import logging
from src.utils.time_utils import now_cst
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.coach_video_classification import CoachVideoClassification
from src.models.expert_tech_point import ActionType, ExpertTechPoint
from src.models.extraction_job import ExtractionJob
from src.models.kb_conflict import KbConflict
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase
from src.services import knowledge_base_svc
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

    # Feature-019: per-category draft KB（复合键 (tech_category, version)）
    kb_tech_category, kb_version_int = await _ensure_kb_record(session, job, merged)
    inserted = await _persist_merged_points(
        session, job, kb_tech_category, kb_version_int, merged
    )
    await _persist_conflicts(session, job, conflicts)

    # Flip kb_extracted on the classification row — this is the visible
    # side-effect that satisfies Feature-013's ``kb_extracted`` consumers.
    await session.execute(
        update(CoachVideoClassification)
        .where(CoachVideoClassification.cos_object_key == job.cos_object_key)
            .values(kb_extracted=True, updated_at=now_cst())
    )
    await session.commit()

    summary = {
        "merged_items": len(merged),
        "inserted_tech_points": inserted,
        "conflict_items": len(conflicts),
        "degraded_mode": degraded,
        # Feature-019: 回报复合键；保留 kb_version（整数）+ 新增 kb_tech_category
        "kb_tech_category": kb_tech_category,
        "kb_version": kb_version_int,
        "kb_extracted_flag_set": True,
    }
    logger.info(
        "merge_kb job=%s: merged=%d inserted=%d conflicts=%d degraded=%s "
        "kb=(%s, %d)",
        job.id, len(merged), inserted, len(conflicts), degraded,
        kb_tech_category, kb_version_int,
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


async def _ensure_kb_record(
    session: AsyncSession,
    job: ExtractionJob,
    merged: list[MergedPoint],
) -> tuple[str, int]:
    """Resolve (or create) the ``tech_knowledge_bases`` row for this job.

    Feature-019 语义:
      - 每个 extraction_job 产出恰好 1 条 KB（绑 job.tech_category）
      - 若该 job 已产出过（幂等重跑），返回现有记录
      - 否则调用 knowledge_base_svc.create_draft_version 产出新 draft KB
        （per-category MAX(version)+1）

    审计: 若 merged 中存在 action_type != job.tech_category 的点，仅 warning
    不分裂成多条 KB（FR-020：一条 KB 对应一个技术类别，跨类别由上游对齐保证）。
    """
    tech_category = job.tech_category
    if not tech_category:
        raise RuntimeError(
            f"extraction_job {job.id} has empty tech_category; "
            "cannot create per-category KB record"
        )

    # 幂等：同 extraction_job_id 已有 KB 则复用
    existing = (
        await session.execute(
            select(TechKnowledgeBase).where(
                TechKnowledgeBase.extraction_job_id == job.id,
                TechKnowledgeBase.tech_category == tech_category,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.tech_category, existing.version

    # 审计 merged 中不一致的 action_type
    stray = {m.action_type for m in merged if m.action_type and m.action_type != tech_category}
    if stray:
        logger.warning(
            "merge_kb._ensure_kb_record: merged items carry action_type %r "
            "that disagrees with job.tech_category=%r (KB written under %r)",
            sorted(stray), tech_category, tech_category,
        )

    # Feature-019: 通过 svc 创建 draft（per-category MAX+1 自增）
    kb = await knowledge_base_svc.create_draft_version(
        session,
        tech_category=tech_category,
        extraction_job_id=job.id,
        point_count=0,   # point_count 由 _persist_merged_points 后续 UPDATE
        notes=(
            f"F-014 draft KB for extraction_job={job.id} "
            f"cos_key={job.cos_object_key} tech_category={tech_category}"
        ),
    )
    return kb.tech_category, kb.version


async def _persist_merged_points(
    session: AsyncSession,
    job: ExtractionJob,
    kb_tech_category: str,
    kb_version: int,
    merged: list[MergedPoint],
) -> int:
    """INSERT one ExpertTechPoint row per merged item. Returns inserted count.

    Feature-019: expert_tech_points 绑复合 FK (kb_tech_category, kb_version)。

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
                kb_tech_category=kb_tech_category,
                kb_version=kb_version,
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
                # 迁移 0015 / 方案 C2：保留提交时的 tech_category 以供审计对账。
                submitted_tech_category=job.tech_category,
            )
        )
        inserted += 1

    # Bump point_count on the KB row (Feature-019 复合键)
    if inserted:
        await session.execute(
            update(TechKnowledgeBase)
            .where(
                TechKnowledgeBase.tech_category == kb_tech_category,
                TechKnowledgeBase.version == kb_version,
            )
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
