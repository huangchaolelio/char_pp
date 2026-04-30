"""Feature-020 · US5 · GET /api/v1/diagnosis-reports 路由.

提供运动员诊断报告的聚合查询能力，支持按 athlete_id / athlete_name /
tech_category / cos_object_key / preprocessing_job_id / source / 时间窗
过滤，按 created_at 或 overall_score 排序。

契约: `specs/020-athlete-inference-pipeline/contracts/athlete_reports_list.md`

设计说明（章程原则 IX · 分层职责）:
  - 本查询为"纯聚合读"且无业务状态转换，直接由路由层组装 SQL，
    避免为单次查询新建 service；复杂度与 `tasks.py` 的 list 端点对称。
  - 按 athlete_* 过滤时 JOIN `athlete_video_classifications.cos_object_key`；
    不按 athlete_* 过滤时不 JOIN（性能优化）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.enums import validate_enum_choice
from src.api.errors import AppException, ErrorCode
from src.api.schemas.envelope import SuccessEnvelope, page as page_envelope
from src.db.session import get_db
from src.models.athlete_video_classification import AthleteVideoClassification
from src.models.diagnosis_report import DiagnosisReport
from src.services.tech_classifier import TECH_CATEGORIES

from pydantic import BaseModel

router = APIRouter(tags=["diagnosis-reports"])


class DiagnosisReportListItem(BaseModel):
    """单份诊断报告的列表视图（不含 dimensions）."""

    id: UUID
    tech_category: str
    overall_score: float
    standard_id: int
    standard_version: int
    cos_object_key: Optional[str] = None
    preprocessing_job_id: Optional[UUID] = None
    source: str
    created_at: datetime


_ALLOWED_SORT_BY = {"created_at", "overall_score"}
_ALLOWED_ORDER = {"asc", "desc"}
_ALLOWED_SOURCE = {"legacy", "athlete_pipeline"}


@router.get(
    "/diagnosis-reports",
    response_model=SuccessEnvelope[list[DiagnosisReportListItem]],
    summary="列出运动员诊断报告（聚合查询）",
)
async def list_diagnosis_reports(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(
        20, ge=1, le=100, description="每页数量，最大 100"
    ),
    athlete_id: Optional[UUID] = Query(None, description="按运动员 UUID 过滤"),
    athlete_name: Optional[str] = Query(None, description="按运动员姓名精确匹配"),
    tech_category: Optional[str] = Query(None, description="21 类技术之一"),
    cos_object_key: Optional[str] = Query(None, description="素材 COS key 反查"),
    preprocessing_job_id: Optional[UUID] = Query(None, description="预处理 job 反查"),
    source: Optional[str] = Query(
        None, description="legacy | athlete_pipeline；默认返回两者"
    ),
    created_after: Optional[datetime] = Query(None),
    created_before: Optional[datetime] = Query(None),
    sort_by: str = Query("created_at"),
    order: str = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[DiagnosisReportListItem]]:
    """List diagnosis reports with rich filtering & sorting.

    `athlete_id` 与 `athlete_name` 同时传入 ⇒ 以 `athlete_id` 为准。
    """
    # ── Enum validation (400 INVALID_ENUM_VALUE) ────────────────────────
    sort_by = validate_enum_choice(
        sort_by, field="sort_by", allowed=_ALLOWED_SORT_BY
    )
    order = validate_enum_choice(order, field="order", allowed=_ALLOWED_ORDER)
    if source is not None:
        source = validate_enum_choice(
            source, field="source", allowed=_ALLOWED_SOURCE
        )
    if tech_category is not None:
        tech_category = validate_enum_choice(
            tech_category,
            field="tech_category",
            allowed=set(TECH_CATEGORIES),
        )

    # ── Build base statement ────────────────────────────────────────────
    # 仅在需要按 athlete 过滤时才 JOIN（性能优化）
    need_athlete_join = athlete_id is not None or athlete_name is not None

    if need_athlete_join:
        base_stmt = (
            select(DiagnosisReport)
            .join(
                AthleteVideoClassification,
                AthleteVideoClassification.cos_object_key
                == DiagnosisReport.cos_object_key,
            )
        )
    else:
        base_stmt = select(DiagnosisReport)

    # ── Apply filters ──────────────────────────────────────────────────
    if athlete_id is not None:
        # athlete_id 存在则忽略 athlete_name（契约约定）
        base_stmt = base_stmt.where(
            AthleteVideoClassification.athlete_id == athlete_id
        )
    elif athlete_name is not None:
        base_stmt = base_stmt.where(
            AthleteVideoClassification.athlete_name == athlete_name
        )

    if tech_category is not None:
        base_stmt = base_stmt.where(DiagnosisReport.tech_category == tech_category)
    if cos_object_key is not None:
        base_stmt = base_stmt.where(DiagnosisReport.cos_object_key == cos_object_key)
    if preprocessing_job_id is not None:
        base_stmt = base_stmt.where(
            DiagnosisReport.preprocessing_job_id == preprocessing_job_id
        )
    if source is not None:
        base_stmt = base_stmt.where(DiagnosisReport.source == source)
    if created_after is not None:
        base_stmt = base_stmt.where(DiagnosisReport.created_at >= created_after)
    if created_before is not None:
        base_stmt = base_stmt.where(DiagnosisReport.created_at <= created_before)

    # ── Count ─────────────────────────────────────────────────────────
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = int((await db.execute(count_stmt)).scalar_one())

    # ── Sort + paginate ───────────────────────────────────────────────
    sort_col = (
        DiagnosisReport.overall_score
        if sort_by == "overall_score"
        else DiagnosisReport.created_at
    )
    if order == "desc":
        base_stmt = base_stmt.order_by(sort_col.desc(), DiagnosisReport.id.desc())
    else:
        base_stmt = base_stmt.order_by(sort_col.asc(), DiagnosisReport.id.asc())

    offset = (page - 1) * page_size
    base_stmt = base_stmt.offset(offset).limit(page_size)

    result = await db.execute(base_stmt)
    rows = result.scalars().all()

    items = [
        DiagnosisReportListItem(
            id=row.id,
            tech_category=row.tech_category,
            overall_score=row.overall_score,
            standard_id=row.standard_id,
            standard_version=row.standard_version,
            cos_object_key=row.cos_object_key,
            preprocessing_job_id=row.preprocessing_job_id,
            source=row.source,
            created_at=row.created_at,
        )
        for row in rows
    ]

    return page_envelope(items, page=page, page_size=page_size, total=total)
