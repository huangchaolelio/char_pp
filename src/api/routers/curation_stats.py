"""Feature-021 · curation_stats router (US5 P3).

  GET /api/v1/curation-stats?group_by=...   (US5 跨教练/类别/规范版本聚合观测)

按 ``contracts/curation_stats.md``：本接口只读，仅统计 ``video_curation_jobs.status='success'``
的作业；样本量 < 5 的分组项附 ``low_sample=true`` 标记，避免聚合可信度被拉偏。

路由层只做枚举校验 + 分页 + 响应封装；聚合逻辑在
:func:`src.services.curation.curation_service.aggregate_curation_stats`.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.curation import CurationStatsItem
from src.api.schemas.envelope import SuccessEnvelope, page as page_envelope
from src.db.session import get_db
from src.services.curation.curation_service import aggregate_curation_stats

router = APIRouter(tags=["curation"])


_GROUP_BY_VALUES = ("coach", "tech_category", "rubric_version")


@router.get(
    "/curation-stats",
    status_code=200,
    response_model=SuccessEnvelope[list[CurationStatsItem]],
    summary="跨教练 / 类别 / 规范版本聚合清洗有效率（US5 P3）",
)
async def get_curation_stats(
    group_by: Literal["coach", "tech_category", "rubric_version"] = Query(
        ...,
        description="分组维度：coach / tech_category / rubric_version 三选一",
    ),
    coach_name: str | None = Query(
        None,
        description="可选过滤：限定教练（与 group_by=coach 同时使用即"
        '"按指定教练单条返回"）',
    ),
    tech_category: str | None = Query(
        None, description="可选过滤：限定技术类别"
    ),
    rubric_version: str | None = Query(
        None,
        pattern=r"^v[0-9]+$",
        description="可选过滤：限定规范版本（如 'v1'）",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[CurationStatsItem]]:
    """spec FR-013 / contracts/curation_stats.md.

    校验：``group_by`` 必传且枚举（Pydantic Literal 拦截，缺失返回 422 VALIDATION_FAILED）；
    ``page_size`` 越界返回 422（Pydantic Query ``le=100`` 拦截）。
    """
    # group_by 由 Literal 类型拦截（Pydantic 422）；这里再做服务层兜底，主要为契约对齐
    if group_by not in _GROUP_BY_VALUES:
        raise AppException(
            ErrorCode.VALIDATION_FAILED,
            message=f"group_by must be one of {_GROUP_BY_VALUES}",
            details={"field": "group_by", "value": group_by},
        )

    items_dto, total = await aggregate_curation_stats(
        db,
        group_by=group_by,
        coach_name=coach_name,
        tech_category=tech_category,
        rubric_version=rubric_version,
        page=page,
        page_size=page_size,
    )

    items = [
        CurationStatsItem(
            coach_name=it.coach_name,
            tech_category=it.tech_category,
            curation_rubric_version=it.curation_rubric_version,
            video_count=it.video_count,
            avg_accepted_duration_ratio=it.avg_accepted_duration_ratio,
            avg_validity_score=it.avg_validity_score,
            low_quality_video_count=it.low_quality_video_count,
            with_overrides_video_count=it.with_overrides_video_count,
            low_sample=it.low_sample,
        )
        for it in items_dto
    ]

    return page_envelope(items, page=page, page_size=page_size, total=total)
