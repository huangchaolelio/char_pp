"""TechStandardBuilder — aggregates ExpertTechPoints into versioned TechStandard records.

Aggregation algorithm (per dimension):
  ideal = median of param_ideal values from valid points
  min   = P25 (25th percentile)
  max   = P75 (75th percentile)

Valid point criteria:
  - extraction_confidence >= 0.7
  - conflict_flag = False

Source quality:
  - multi_source: distinct source_video_id count >= 2 (proxy for coach diversity)
  - single_source: distinct source_video_id count == 1
  - skip: no valid points (distinct source_video_id count == 0)

Version management:
  - Each build creates a new version (auto-increment per tech_category)
  - Previous active version is archived before new one is inserted
  - All operations are within a single transaction
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.models.expert_tech_point import ExpertTechPoint
from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase
from src.models.tech_standard import SourceQuality, StandardStatus, TechStandard, TechStandardPoint
from src.services.tech_classifier import TECH_CATEGORIES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helper functions (testable without DB)
# ---------------------------------------------------------------------------

def filter_valid_points(points: Sequence) -> list:
    """Return only points with confidence >= 0.7 and conflict_flag=False."""
    return [
        p for p in points
        if p.extraction_confidence >= 0.7 and not p.conflict_flag
    ]


def _aggregate_dimension(values: list[float]) -> dict[str, float]:
    """Compute median + P25/P75 for a list of float values.

    Returns dict with keys: ideal, min, max.
    """
    arr = np.array(values, dtype=float)
    return {
        "ideal": float(np.median(arr)),
        "min": float(np.percentile(arr, 25)),
        "max": float(np.percentile(arr, 75)),
    }


def determine_source_quality(source_video_ids: list) -> Optional[str]:
    """Determine source_quality from distinct source video IDs.

    Returns:
      'multi_source'  if count >= 2
      'single_source' if count == 1
      None            if count == 0 (signals skip)
    """
    unique_count = len(set(str(v) for v in source_video_ids))
    if unique_count == 0:
        return None
    if unique_count >= 2:
        return SourceQuality.multi_source.value
    return SourceQuality.single_source.value


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BuildResult:
    """Result of building a single tech_category standard."""

    tech_category: str
    result: str          # "success" | "skipped" | "failed"
    reason: Optional[str] = None
    standard_id: Optional[int] = None
    version: Optional[int] = None
    dimension_count: Optional[int] = None
    coach_count: Optional[int] = None


@dataclass
class BatchBuildResult:
    """Result of a batch build across all tech categories."""

    results: List[BuildResult] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.result == "success")

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.results if r.result == "skipped")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.result == "failed")


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------

class TechStandardBuilder:
    """Builds and persists versioned TechStandard records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build_standard(self, tech_category: str) -> BuildResult:
        """Build or rebuild the standard for a single tech_category (Feature-019).

        Feature-019 契约改变（spec.md FR-014~FR-019）：
          - 数据源 MUST 限定为 "该 tech_category 当前 active KB 所含 expert_tech_points"
          - 若该类别无 active KB ⇒ 抛 `NO_ACTIVE_KB_FOR_CATEGORY`（409）
          - 指纹幂等：同一 active KB + 相同 points 集合 ⇒ `STANDARD_ALREADY_UP_TO_DATE`（409）
          - 新产出的 TechStandard 直接 status=active（FR-014a，不走 draft）

        步骤:
          1. 查该类别 active KB，无则抛
          2. 从该 KB 下取 valid ExpertTechPoints（confidence >= 0.7 且 conflict_flag=False）
          3. 计算 source_fingerprint → 对比同类别现 active 标准的指纹，一致则抛
          4. 按 dimension 聚合 → median + P25/P75
          5. 归档同类别旧 active standard（仅同类别）
          6. INSERT 新 TechStandard → status='active' + source_fingerprint
          7. INSERT TechStandardPoints

        Returns BuildResult（result='success' 或 'skipped'）。
        """
        session = self._session

        # --- Step 1: 查该类别 active KB ---------------------------
        active_kb = (await session.execute(
            select(TechKnowledgeBase).where(
                TechKnowledgeBase.tech_category == tech_category,
                TechKnowledgeBase.status == KBStatus.active,
            )
        )).scalar_one_or_none()
        if active_kb is None:
            raise AppException(
                ErrorCode.NO_ACTIVE_KB_FOR_CATEGORY,
                details={"tech_category": tech_category},
            )

        # --- Step 2: 取该 active KB 下的 valid ExpertTechPoints ---
        stmt = select(ExpertTechPoint).where(
            ExpertTechPoint.kb_tech_category == tech_category,
            ExpertTechPoint.kb_version == active_kb.version,
            ExpertTechPoint.extraction_confidence >= 0.7,
            ExpertTechPoint.conflict_flag.is_(False),
        )
        rows = await session.execute(stmt)
        all_points: list[ExpertTechPoint] = list(rows.scalars().all())

        if not all_points:
            logger.info(
                "build_standard skipped: no_valid_points",
                extra={"tech_category": tech_category, "kb_version": active_kb.version},
            )
            return BuildResult(
                tech_category=tech_category,
                result="skipped",
                reason="no_valid_points",
            )

        # --- Step 3: 幂等检查 source_fingerprint ---------------------
        # FR-019 口径：sha256(sorted_json([(ep.id, ep.param_ideal, ep.extraction_confidence) for ep in points]))
        fingerprint_src = sorted(
            [
                [str(p.id), float(p.param_ideal), float(p.extraction_confidence)]
                for p in all_points
            ]
        )
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_src, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()

        existing_active = (await session.execute(
            select(TechStandard).where(
                TechStandard.tech_category == tech_category,
                TechStandard.status == StandardStatus.active.value,
            )
        )).scalar_one_or_none()
        if existing_active is not None and existing_active.source_fingerprint == fingerprint:
            raise AppException(
                ErrorCode.STANDARD_ALREADY_UP_TO_DATE,
                details={
                    "tech_category": tech_category,
                    "current_version": existing_active.version,
                    "source_kb_version": active_kb.version,
                },
            )

        # --- Step 4: 按 dimension 聚合 ----------------------------------
        dim_map: dict[str, list[float]] = {}
        dim_unit: dict[str, Optional[str]] = {}
        dim_videos: dict[str, list] = {}
        all_video_ids: list = []

        for p in all_points:
            dim = p.dimension
            if dim not in dim_map:
                dim_map[dim] = []
                dim_videos[dim] = []
                dim_unit[dim] = getattr(p, "unit", None)
            dim_map[dim].append(float(p.param_ideal))
            vid = p.source_video_id
            dim_videos[dim].append(vid)
            all_video_ids.append(vid)

        source_quality = determine_source_quality(all_video_ids)
        if source_quality is None:
            return BuildResult(
                tech_category=tech_category,
                result="skipped",
                reason="no_valid_points",
            )

        # --- Step 5: 计算下一 version + 归档同类别旧 active ---------
        version_stmt = (
            select(TechStandard.version)
            .where(TechStandard.tech_category == tech_category)
            .order_by(TechStandard.version.desc())
            .limit(1)
        )
        version_row = await session.execute(version_stmt)
        last_version = version_row.scalar_one_or_none()
        next_version = (last_version or 0) + 1

        archive_stmt = (
            update(TechStandard)
            .where(
                TechStandard.tech_category == tech_category,
                TechStandard.status == StandardStatus.active.value,
            )
            .values(status=StandardStatus.archived.value)
        )
        await session.execute(archive_stmt)

        # --- Step 6: 聚合 standard_points --------------------------------
        standard_points: list[dict[str, Any]] = []
        total_point_count = 0
        for dim, values in dim_map.items():
            try:
                agg = _aggregate_dimension(values)
            except Exception as exc:
                logger.warning(
                    "Skipping dimension due to aggregation error",
                    extra={"dimension": dim, "tech_category": tech_category, "error": str(exc)},
                )
                continue
            standard_points.append(
                {
                    "dimension": dim,
                    "ideal": agg["ideal"],
                    "min": agg["min"],
                    "max": agg["max"],
                    "unit": dim_unit.get(dim),
                    "sample_count": len(values),
                    "coach_count": len(set(str(v) for v in dim_videos[dim])),
                }
            )
            total_point_count += len(values)

        # --- Step 7: Insert 新 standard（status 直接 active） ------------
        standard = TechStandard(
            tech_category=tech_category,
            version=next_version,
            status=StandardStatus.active.value,
            source_quality=source_quality,
            coach_count=len(set(str(v) for v in all_video_ids)),
            point_count=total_point_count,
            source_fingerprint=fingerprint,
        )
        session.add(standard)
        await session.flush()  # get standard.id

        for sp in standard_points:
            point = TechStandardPoint(
                standard_id=standard.id,
                **sp,
            )
            session.add(point)

        await session.flush()

        logger.info(
            "build_standard success",
            extra={
                "tech_category": tech_category,
                "version": next_version,
                "coach_count": standard.coach_count,
                "point_count": total_point_count,
                "dimension_count": len(standard_points),
                "source_quality": source_quality,
                "source_kb_version": active_kb.version,
            },
        )

        return BuildResult(
            tech_category=tech_category,
            result="success",
            standard_id=standard.id,
            version=next_version,
            dimension_count=len(standard_points),
            coach_count=standard.coach_count,
        )

    async def build_all(self) -> BatchBuildResult:
        """Build standards for all valid ActionType values.

        Iterates over ExpertTechPoint ActionType enum values (not TECH_CATEGORIES,
        which is used for video classification). Each category is built independently;
        failures do not block others.

        Returns BatchBuildResult with per-category results and aggregate counts.
        """
        from src.models.expert_tech_point import ActionType as EtpActionType

        results: list[BuildResult] = []

        for action_type in EtpActionType:
            try:
                result = await self.build_standard(action_type.value)
                results.append(result)
            except Exception as exc:
                logger.error(
                    "build_standard failed",
                    extra={"tech_category": action_type.value, "error": str(exc)},
                )
                results.append(
                    BuildResult(
                        tech_category=action_type.value,
                        result="failed",
                        reason=str(exc),
                    )
                )

        batch = BatchBuildResult(results=results)
        logger.info(
            "build_all complete",
            extra={
                "success": batch.success_count,
                "skipped": batch.skipped_count,
                "failed": batch.failed_count,
            },
        )
        return batch


# ---------------------------------------------------------------------------
# DB query helpers (used by API router)
# ---------------------------------------------------------------------------

async def get_active_standard(
    session: AsyncSession, tech_category: str
) -> Optional[TechStandard]:
    """Return the active TechStandard for tech_category, or None."""
    stmt = (
        select(TechStandard)
        .where(
            TechStandard.tech_category == tech_category,
            TechStandard.status == StandardStatus.active.value,
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_active_standards(
    session: AsyncSession,
    source_quality: Optional[str] = None,
) -> list[TechStandard]:
    """Return all active TechStandards, optionally filtered by source_quality."""
    stmt = select(TechStandard).where(
        TechStandard.status == StandardStatus.active.value
    )
    if source_quality:
        stmt = stmt.where(TechStandard.source_quality == source_quality)
    result = await session.execute(stmt)
    return list(result.scalars().all())
