"""Standards API router — /api/v1/standards.

Endpoints:
  POST /standards/build          Trigger single or batch standard build
  GET  /standards/{tech_category} Query active standard for a tech category
  GET  /standards                List all active standards summary

Feature-017: 响应体统一迁移至 ``SuccessEnvelope``；``HTTPException``
改为 ``AppException``；裸字符串错误码（如 ``standard_not_found``）映射到 ``ErrorCode``
枚举（章程 v1.4.0 原则 IX）。
"""

from __future__ import annotations

import uuid
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.envelope import SuccessEnvelope, ok
from src.db.session import get_db
from src.models.expert_tech_point import ActionType as EtpActionType
from src.services.tech_standard_builder import (
    get_active_standard,
    list_active_standards,
    TechStandardBuilder,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/standards", tags=["standards"])

# Set of valid action_type string values for validation
_VALID_ACTION_TYPES: set[str] = {at.value for at in EtpActionType}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class BuildRequest(BaseModel):
    tech_category: Optional[str] = None

    @field_validator("tech_category")
    @classmethod
    def validate_tech_category(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_ACTION_TYPES:
            raise ValueError(f"{v!r} is not a valid tech category")
        return v


class DimensionResponse(BaseModel):
    dimension: str
    ideal: float
    min: float
    max: float
    unit: Optional[str]
    sample_count: int
    coach_count: int


class StandardResponse(BaseModel):
    tech_category: str
    standard_id: int
    version: int
    source_quality: str
    coach_count: int
    point_count: int
    built_at: str
    dimensions: list[DimensionResponse]


class StandardSummaryItem(BaseModel):
    tech_category: str
    standard_id: int
    version: int
    source_quality: str
    coach_count: int
    dimension_count: int
    built_at: str


class StandardsListData(BaseModel):
    """List payload for active standards (with missing_categories metadata).

    ``meta`` 字段的分页语义不适用此列表端点（固定返回全量+缺失分类集合），
    采用自定义 data 结构放进 ``SuccessEnvelope[StandardsListData]``。
    """

    standards: list[StandardSummaryItem]
    total: int
    missing_categories: list[str]


class BuildResultResponse(BaseModel):
    result: str
    reason: Optional[str] = None
    standard_id: Optional[int] = None
    version: Optional[int] = None
    dimension_count: Optional[int] = None
    coach_count: Optional[int] = None


class SingleBuildResponse(BaseModel):
    task_id: str
    mode: str
    tech_category: str
    result: BuildResultResponse


class BatchSummary(BaseModel):
    success_count: int
    skipped_count: int
    failed_count: int


class BatchBuildResponse(BaseModel):
    task_id: str
    mode: str
    results: list[dict[str, Any]]
    summary: BatchSummary


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/build", response_model=SuccessEnvelope[dict[str, Any]])
async def build_standard(
    request: BuildRequest,
    session: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[dict[str, Any]]:
    """Trigger single or batch tech standard build.

    - With tech_category: build single category
    - Without tech_category: build all ActionType categories

    响应 ``data`` 结构由 ``mode`` 字段判别：
    - ``mode="single"``: ``SingleBuildResponse`` 展开字段
    - ``mode="batch"``: ``BatchBuildResponse`` 展开字段
    """
    builder = TechStandardBuilder(session)
    task_id = str(uuid.uuid4())

    if request.tech_category:
        # Single build
        result = await builder.build_standard(request.tech_category)
        await session.commit()

        single = SingleBuildResponse(
            task_id=task_id,
            mode="single",
            tech_category=request.tech_category,
            result=BuildResultResponse(
                result=result.result,
                reason=result.reason,
                standard_id=result.standard_id,
                version=result.version,
                dimension_count=result.dimension_count,
                coach_count=result.coach_count,
            ),
        )
        return ok(single.model_dump())

    # Batch build
    batch = await builder.build_all()
    await session.commit()

    results_data = [
        {
            "tech_category": r.tech_category,
            "result": r.result,
            "reason": r.reason,
            "standard_id": r.standard_id,
            "version": r.version,
            "dimension_count": r.dimension_count,
            "coach_count": r.coach_count,
        }
        for r in batch.results
    ]
    batch_resp = BatchBuildResponse(
        task_id=task_id,
        mode="batch",
        results=results_data,
        summary=BatchSummary(
            success_count=batch.success_count,
            skipped_count=batch.skipped_count,
            failed_count=batch.failed_count,
        ),
    )
    return ok(batch_resp.model_dump())


@router.get(
    "/{tech_category}",
    response_model=SuccessEnvelope[StandardResponse],
)
async def get_standard(
    tech_category: str,
    session: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[StandardResponse]:
    """Query the active standard for a given tech_category.

    Returns 404 ``NOT_FOUND`` (tech standard 无资源专属 code，使用通用 NOT_FOUND)
    if no active standard exists for this category.
    """
    standard = await get_active_standard(session, tech_category)
    if standard is None:
        raise AppException(
            ErrorCode.NOT_FOUND,
            message=f"No active standard for tech_category: {tech_category}",
            details={"resource": "tech_standard", "tech_category": tech_category},
        )

    return ok(StandardResponse(
        tech_category=standard.tech_category,
        standard_id=standard.id,
        version=standard.version,
        source_quality=standard.source_quality,
        coach_count=standard.coach_count,
        point_count=standard.point_count,
        built_at=standard.built_at.isoformat(),
        dimensions=[
            DimensionResponse(
                dimension=p.dimension,
                ideal=p.ideal,
                min=p.min,
                max=p.max,
                unit=p.unit,
                sample_count=p.sample_count,
                coach_count=p.coach_count,
            )
            for p in standard.points
        ],
    ))


@router.get("", response_model=SuccessEnvelope[StandardsListData])
async def list_standards(
    source_quality: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[StandardsListData]:
    """List all active tech standards with summary info.

    Optional filter: source_quality=multi_source|single_source
    Returns missing_categories: action types with no active standard.
    """
    standards = await list_active_standards(session, source_quality=source_quality)

    existing_categories = {s.tech_category for s in standards}
    all_action_types = {at.value for at in EtpActionType}
    missing = sorted(all_action_types - existing_categories)

    items = [
        StandardSummaryItem(
            tech_category=s.tech_category,
            standard_id=s.id,
            version=s.version,
            source_quality=s.source_quality,
            coach_count=s.coach_count,
            dimension_count=len(s.points),
            built_at=s.built_at.isoformat(),
        )
        for s in standards
    ]

    return ok(StandardsListData(
        standards=items,
        total=len(items),
        missing_categories=missing,
    ))
