"""visual_kb_extract executor (Feature 015) — real pose-rule extraction.

Pipeline:
    1. Read the ``pose_analysis`` artifact (pose.json) via ``artifact_io``.
    2. Short-circuit on empty frames → succeed with empty ``kb_items``
       (merge_kb will degrade gracefully if audio side is also empty).
    3. Detect action segments (``action_segmenter.segment_actions``).
    4. For each segment, classify it and run the per-dimension
       ``tech_extractor.extract_tech_points`` rules.
    5. Project each ``TechDimension`` into the ``kb_items`` dict format
       that ``merge_kb`` consumes.

The executor never crashes on degraded input — empty artifacts, classified
segments with no high-confidence dimensions, etc., are normal production
outcomes, not failures.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.services import action_classifier, action_segmenter, tech_extractor
from src.services.action_classifier import ClassifiedSegment
from src.services.action_segmenter import ActionSegment, frames_for_segment
from src.services.kb_extraction_pipeline.artifact_io import read_pose_artifact
from src.services.pose_estimator import FramePoseResult


logger = logging.getLogger(__name__)

# Feature-002 tech_extractor's confidence threshold (≥0.7 passes).
_CONFIDENCE_THRESHOLD = 0.7


async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Produce visual ``kb_items`` from the pose analysis artifact."""
    pose_path = (
        await session.execute(
            select(PipelineStep.output_artifact_path).where(
                PipelineStep.job_id == job.id,
                PipelineStep.step_type == StepType.pose_analysis,
            )
        )
    ).scalar_one_or_none()
    if not pose_path:
        raise RuntimeError(
            "pose_analysis artifact missing — cannot run visual extraction"
        )

    pose_path_obj = Path(pose_path)
    # artifact_io tolerates missing files; we still return success with empty
    # items rather than raising, matching FR-002 / FR-007 semantics.
    video_meta, backend_in, frames = await asyncio.to_thread(
        read_pose_artifact, pose_path_obj
    )

    # Back-compat path: some Feature-014 fixtures embed a raw ``kb_items``
    # list in pose.json instead of a real frame sequence. Honour that if the
    # frame sequence is empty — the integration tests for audio-enhanced KB
    # extraction depend on this.
    if not frames:
        legacy_items = _read_legacy_kb_items(pose_path_obj, job.tech_category)
        return {
            "status": PipelineStepStatus.success,
            "output_summary": {
                "kb_items": legacy_items,
                "kb_items_count": len(legacy_items),
                "source_type": "visual",
                "tech_category": job.tech_category,
                "backend": backend_in if backend_in != "unknown" else "pose_rule",
                "segments_processed": 0,
                "segments_skipped_low_confidence": 0,
            },
            "output_artifact_path": None,
        }

    # ── Real pose → classification → tech_extractor pipeline ─────────────────
    # 短期对齐契约（Feature 审计修复）：
    #   规则分类器 v1 只能识别 forehand_topspin / backhand_push 两类，其余
    #   19/21 类必回落为 "unknown"。历史上我们按分类器输出当权威标签写入，
    #   事实上绝大多数场景都是悄悄退化为 job.tech_category——不如把这个契约
    #   显式化：visual_kb_extract 的 action_type 一律取 job.tech_category，
    #   分类器只用于审计（disagreement 计数），不再影响落库内容。
    #   这样 `tech_knowledge_bases.action_types_covered` 与提交类别一定一致，
    #   避免出现"提交 forehand_topspin / 入库 backhand_push"的错配。
    def _run_extraction() -> tuple[list[dict], int, int, int]:
        segments: list[ActionSegment] = action_segmenter.segment_actions(frames)
        items: list[dict] = []
        segments_skipped = 0
        classifier_disagreements = 0
        for segment in segments:
            segment_frames = frames_for_segment(frames, segment)
            classified: ClassifiedSegment = action_classifier.classify_segment(
                segment_frames, segment
            )
            # 审计：分类器规则给出的标签若与提交类别不一致，记录 WARNING。
            # 不阻断落库，仅用于分类器偏差对账（submitted_tech_category）。
            if (
                classified.action_type
                and classified.action_type != "unknown"
                and classified.action_type != job.tech_category
            ):
                classifier_disagreements += 1
                logger.warning(
                    "visual_kb_extract job=%s segment=%dms-%dms: classifier=%r "
                    "disagrees with job.tech_category=%r (keeping tech_category)",
                    job.id, segment.start_ms, segment.end_ms,
                    classified.action_type, job.tech_category,
                )

            result = tech_extractor.extract_tech_points(
                classified, frames, confidence_threshold=_CONFIDENCE_THRESHOLD
            )
            if not result.dimensions:
                segments_skipped += 1
                continue
            for dim in result.dimensions:
                items.append({
                    "dimension": dim.dimension,
                    "param_min": float(dim.param_min),
                    "param_max": float(dim.param_max),
                    "param_ideal": float(dim.param_ideal),
                    "unit": dim.unit,
                    "extraction_confidence": float(dim.extraction_confidence),
                    # 显式契约：始终按提交类别落库，禁止分类器自由覆盖。
                    "action_type": job.tech_category,
                    "source_type": "visual",
                })
        return items, len(segments), segments_skipped, classifier_disagreements

    (
        kb_items,
        segments_processed,
        segments_skipped,
        classifier_disagreements,
    ) = await asyncio.to_thread(_run_extraction)

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "kb_items": kb_items,
            "kb_items_count": len(kb_items),
            "source_type": "visual",
            "tech_category": job.tech_category,
            "backend": "action_segmenter+tech_extractor",
            "segments_processed": segments_processed,
            "segments_skipped_low_confidence": segments_skipped,
            "classifier_disagreements": classifier_disagreements,
        },
        "output_artifact_path": None,
    }


def _read_legacy_kb_items(pose_path: Path, tech_category: str) -> list[dict]:
    """Fallback reader for fixtures that embed ``kb_items`` in pose.json.

    Kept for compatibility with Feature-014 integration tests that don't
    synthesise a full frame list. Real production flows always write pose
    artifacts through ``artifact_io.write_pose_artifact`` and therefore
    never hit this branch.
    """
    import json

    try:
        data = json.loads(pose_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    raw_items = data.get("kb_items")
    if not isinstance(raw_items, list):
        return []

    cleaned: list[dict] = []
    for raw in raw_items:
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
            "extraction_confidence": float(raw.get("extraction_confidence", 0.8)),
            # 短期对齐契约：visual 侧一律按提交类别落库（忽略 fixture 中的 action_type）。
            "action_type": tech_category,
            "source_type": "visual",
        })
    return cleaned
