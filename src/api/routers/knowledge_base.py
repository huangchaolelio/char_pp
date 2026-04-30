"""Knowledge base router — Feature-019 per-category lifecycle.

Breaking changes vs Feature-014/017:
  - GET  /knowledge-base/versions                              — list + filter + paginate
  - GET  /knowledge-base/versions/{tech_category}/{version}    — detail (composite key)
  - POST /knowledge-base/versions/{tech_category}/{version}/approve — approve

老路径（单列 version 主键）均保留 **ENDPOINT_RETIRED 哨兵**（章程原则 IX）:
  - POST /knowledge-base/{version}/approve        → ENDPOINT_RETIRED
  - GET  /knowledge-base/{version}                → ENDPOINT_RETIRED

Feature-019 不再通过 ``?business_phase=`` 过滤（已不适用，KB 恒为 STANDARDIZATION）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.envelope import SuccessEnvelope, ok, page as page_envelope
from src.api.schemas.knowledge_base import (
    ApproveKbRequest,
    ApproveKbResponse,
    DimensionsSummary,
    KbVersionDetail,
    KbVersionItem,
    TipsUpdatedStats,
)
from src.db.session import get_db
from src.models.tech_knowledge_base import KBStatus
from src.services import knowledge_base_svc

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


# ── Helpers ────────────────────────────────────────────────────────────────

def _to_item(kb) -> KbVersionItem:
    """TechKnowledgeBase ORM → KbVersionItem DTO."""
    return KbVersionItem(
        tech_category=kb.tech_category,
        version=kb.version,
        status=kb.status.value,
        point_count=kb.point_count,
        extraction_job_id=str(kb.extraction_job_id),
        approved_by=kb.approved_by,
        approved_at=kb.approved_at,
        created_at=kb.created_at,
        notes=kb.notes,
    )


# ── GET /knowledge-base/versions ───────────────────────────────────────────

@router.get(
    "/versions",
    response_model=SuccessEnvelope[list[KbVersionItem]],
)
async def list_kb_versions(
    tech_category: str | None = Query(None, description="按技术类别过滤（21 类之一）"),
    status: str | None = Query(None, description="按状态过滤（draft/active/archived）"),
    extraction_job_id: uuid.UUID | None = Query(
        None, description="按 extraction_job_id 精确匹配（定位单作业产出的全部 KB）"
    ),
    page_num: int = Query(1, ge=1, alias="page"),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[KbVersionItem]]:
    """Feature-019 US2 — 列表 + 按 tech_category/status/extraction_job_id 过滤 + 分页."""
    # status 枚举校验（服务端小写归一化）
    status_norm: str | None = None
    if status is not None:
        s = status.strip().lower()
        allowed = {e.value for e in KBStatus}
        if s not in allowed:
            raise AppException(
                ErrorCode.INVALID_ENUM_VALUE,
                message=f"status 非法值：{status!r}",
                details={
                    "field": "status",
                    "allowed": sorted(allowed),
                    "got": status,
                },
            )
        status_norm = s

    # tech_category 归一化（小写下划线），非法值不阻断（允许查到空集）
    tc_norm: str | None = None
    if tech_category is not None:
        tc_norm = tech_category.strip().lower()

    items, total = await knowledge_base_svc.list_versions(
        db,
        tech_category=tc_norm,
        status=status_norm,
        extraction_job_id=extraction_job_id,
        page=page_num,
        page_size=page_size,
    )

    return page_envelope(
        [_to_item(kb) for kb in items],
        page=page_num,
        page_size=page_size,
        total=total,
    )


# ── GET /knowledge-base/versions/{tech_category}/{version} ────────────────

@router.get(
    "/versions/{tech_category}/{version}",
    response_model=SuccessEnvelope[KbVersionDetail],
)
async def get_kb_version_detail(
    tech_category: str,
    version: int,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[KbVersionDetail]:
    """Feature-019 US2 — 详情 + expert_tech_points 聚合摘要."""
    tc = tech_category.strip().lower()
    result = await knowledge_base_svc.get_version_detail(db, tc, version)
    if result is None:
        raise AppException(
            ErrorCode.KB_VERSION_NOT_FOUND,
            details={"tech_category": tc, "version": version},
        )
    kb, summary = result
    detail = KbVersionDetail(
        tech_category=kb.tech_category,
        version=kb.version,
        status=kb.status.value,
        point_count=kb.point_count,
        extraction_job_id=str(kb.extraction_job_id),
        approved_by=kb.approved_by,
        approved_at=kb.approved_at,
        created_at=kb.created_at,
        notes=kb.notes,
        dimensions_summary=DimensionsSummary(**summary),
    )
    return ok(detail)


# ── POST /knowledge-base/versions/{tech_category}/{version}/approve ───────

@router.post(
    "/versions/{tech_category}/{version}/approve",
    response_model=SuccessEnvelope[ApproveKbResponse],
)
async def approve_kb_version(
    tech_category: str,
    version: int,
    body: ApproveKbRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[ApproveKbResponse]:
    """Feature-019 US1 — 按单一 tech_category 独立审批 KB 草稿."""
    tc = tech_category.strip().lower()

    # service 层直接抛 AppException（包含 KB_VERSION_NOT_FOUND / KB_VERSION_NOT_DRAFT
    # / KB_EMPTY_POINTS / KB_CONFLICT_UNRESOLVED），路由层无需二次转换
    async with db.begin():
        result = await knowledge_base_svc.approve_version(
            db,
            tech_category=tc,
            version=version,
            approved_by=body.approved_by,
            notes=body.notes,
        )

    return ok(
        ApproveKbResponse(
            new_active=_to_item(result["new_active"]),
            previous_active_version=result["previous_active_version"],
            tips_updated=TipsUpdatedStats(**result["tips_updated"]),
        )
    )


# ── T020 / T027：老路径 ENDPOINT_RETIRED 哨兵（章程原则 IX 强制）──────────

@router.post("/{version}/approve", include_in_schema=False)
async def _retired_approve_single_key(version: str) -> None:
    """老的单列 version 主键 approve 路径 → 返回 ENDPOINT_RETIRED."""
    raise AppException(
        ErrorCode.ENDPOINT_RETIRED,
        details={
            "successor": "/api/v1/knowledge-base/versions/{tech_category}/{version}/approve",
            "migration_note": (
                "Feature-019 将 KB 主键提升为 (tech_category, version) 复合键；"
                "请使用新路径。"
            ),
        },
    )


@router.get("/{version}", include_in_schema=False)
async def _retired_detail_single_key(version: str) -> None:
    """老的单列 version 详情路径 → 返回 ENDPOINT_RETIRED."""
    raise AppException(
        ErrorCode.ENDPOINT_RETIRED,
        details={
            "successor": "/api/v1/knowledge-base/versions/{tech_category}/{version}",
            "migration_note": (
                "Feature-019 详情路径需同时指定 tech_category 与 version。"
            ),
        },
    )
