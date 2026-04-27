"""Classifications router — endpoints for coach video tech classification (Feature 008).

Endpoints:
  POST /api/v1/classifications/scan          — trigger full/incremental scan
  GET  /api/v1/classifications/scan/{task_id} — query scan progress
  GET  /api/v1/classifications               — list classification records
  GET  /api/v1/classifications/summary       — coach/tech breakdown summary
  PATCH /api/v1/classifications/{id}         — manual correction

Feature-017: 响应体统一迁移至 ``SuccessEnvelope``；``HTTPException`` 改为 ``AppException``；
``scan_mode`` 非法从裸字符串改为 ``INVALID_ENUM_VALUE``；``tech_category`` 非法改为
``INVALID_ENUM_VALUE``；``classification record not found`` 改为 ``NOT_FOUND``。
注意：``limit/offset`` 分页参数保留现状（阶段 5 T054 再整改为 ``page/page_size``）。
"""

from __future__ import annotations

import uuid
from typing import Optional

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.classification import (
    ClassificationItem,
    ClassificationPatchRequest,
    ClassificationPatchResponse,
    CoachSummaryItem,
    ScanRequest,
    ScanStatusResponse,
    TechBreakdownItem,
)
from src.api.schemas.envelope import SuccessEnvelope, ok, page
from src.db.session import get_db
from src.models.coach_video_classification import CoachVideoClassification
from src.services.tech_classifier import TECH_CATEGORIES, get_tech_label
from src.workers.celery_app import celery_app

router = APIRouter(tags=["classifications"])


# ── POST /classifications/scan ────────────────────────────────────────────────

@router.post(
    "/classifications/scan",
    status_code=202,
    response_model=SuccessEnvelope[ScanStatusResponse],
    summary="触发教练视频技术分类扫描",
)
async def trigger_scan(body: ScanRequest) -> SuccessEnvelope[ScanStatusResponse]:
    """触发全量或增量扫描，异步 Celery task，返回 task_id。"""
    if body.scan_mode not in ("full", "incremental"):
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            message=f"invalid scan_mode: {body.scan_mode!r}",
            details={
                "field": "scan_mode",
                "value": body.scan_mode,
                "allowed": ["full", "incremental"],
            },
        )

    task_id = str(uuid.uuid4())
    from src.workers.classification_task import scan_cos_videos

    scan_cos_videos.apply_async(
        kwargs={"task_id": task_id, "scan_mode": body.scan_mode},
        task_id=task_id,  # use our task_id as Celery task ID for progress tracking
    )

    return ok(ScanStatusResponse(
        task_id=task_id,
        status="pending",
    ))


# ── GET /classifications/scan/{task_id} ───────────────────────────────────────

@router.get(
    "/classifications/scan/{task_id}",
    response_model=SuccessEnvelope[ScanStatusResponse],
    summary="查询扫描任务进度",
)
async def get_scan_status(task_id: str) -> SuccessEnvelope[ScanStatusResponse]:
    """查询扫描任务状态，从 Celery result backend 获取。"""
    result = AsyncResult(task_id, app=celery_app)

    if result.state == "PENDING":
        # Task not found or still queued
        return ok(ScanStatusResponse(task_id=task_id, status="pending"))
    elif result.state == "RUNNING":
        meta = result.info or {}
        return ok(ScanStatusResponse(
            task_id=task_id,
            status="running",
            scanned=meta.get("scanned"),
            inserted=meta.get("inserted"),
            updated=meta.get("updated"),
            skipped=meta.get("skipped"),
            errors=meta.get("errors"),
            elapsed_s=meta.get("elapsed_s"),
        ))
    elif result.state == "SUCCESS":
        info = result.result or {}
        return ok(ScanStatusResponse(
            task_id=task_id,
            status="success",
            scanned=info.get("scanned"),
            inserted=info.get("inserted"),
            updated=info.get("updated"),
            skipped=info.get("skipped"),
            errors=info.get("errors"),
            elapsed_s=info.get("elapsed_s"),
        ))
    elif result.state == "FAILURE":
        return ok(ScanStatusResponse(
            task_id=task_id,
            status="failed",
            error_detail=str(result.info) if result.info else None,
        ))
    else:
        # RETRY, REVOKED, etc.
        return ok(ScanStatusResponse(task_id=task_id, status=result.state.lower()))


# ── GET /classifications ──────────────────────────────────────────────────────

@router.get(
    "/classifications",
    response_model=SuccessEnvelope[list[ClassificationItem]],
    summary="查询分类记录列表",
)
async def list_classifications(
    coach_name: Optional[str] = Query(None),
    tech_category: Optional[str] = Query(None),
    kb_extracted: Optional[bool] = Query(None),
    classification_source: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[ClassificationItem]]:
    """按条件查询分类记录，支持分页。

    Feature-017 临时保留 ``limit/offset`` 参数；阶段 5 T054 将统一迁移到
    ``page/page_size``。当前使用 ``page(...)`` 构造器时，把 ``offset/limit``
    换算成 ``page/page_size`` 填入 meta 以保证信封结构一致。
    """
    stmt = select(CoachVideoClassification)
    count_stmt = select(func.count()).select_from(CoachVideoClassification)

    if coach_name:
        stmt = stmt.where(CoachVideoClassification.coach_name == coach_name)
        count_stmt = count_stmt.where(CoachVideoClassification.coach_name == coach_name)
    if tech_category:
        stmt = stmt.where(CoachVideoClassification.tech_category == tech_category)
        count_stmt = count_stmt.where(CoachVideoClassification.tech_category == tech_category)
    if kb_extracted is not None:
        stmt = stmt.where(CoachVideoClassification.kb_extracted == kb_extracted)
        count_stmt = count_stmt.where(CoachVideoClassification.kb_extracted == kb_extracted)
    if classification_source:
        stmt = stmt.where(
            CoachVideoClassification.classification_source == classification_source
        )
        count_stmt = count_stmt.where(
            CoachVideoClassification.classification_source == classification_source
        )

    total_result = await session.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = stmt.order_by(CoachVideoClassification.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    records = result.scalars().all()

    items = [ClassificationItem.model_validate(r) for r in records]
    # 换算 offset/limit → page/page_size，阶段 5 T054 再整改为原生 page 参数
    derived_page = (offset // limit) + 1 if limit > 0 else 1
    return page(items, page=derived_page, page_size=limit, total=total)


# ── GET /classifications/summary ──────────────────────────────────────────────

@router.get(
    "/classifications/summary",
    response_model=SuccessEnvelope[list[CoachSummaryItem]],
    summary="按教练统计技术分类汇总",
)
async def get_summary(
    coach_name: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[CoachSummaryItem]]:
    """按教练 + 技术类别统计视频数量和已提取知识库数量."""
    # Group by coach_name, tech_category
    stmt = select(
        CoachVideoClassification.coach_name,
        CoachVideoClassification.tech_category,
        func.count().label("count"),
        func.sum(
            func.cast(CoachVideoClassification.kb_extracted, type_=None)
        ).label("kb_count"),
    ).group_by(
        CoachVideoClassification.coach_name,
        CoachVideoClassification.tech_category,
    )
    if coach_name:
        stmt = stmt.where(CoachVideoClassification.coach_name == coach_name)
    stmt = stmt.order_by(CoachVideoClassification.coach_name)

    result = await session.execute(stmt)
    rows = result.all()

    # Build coach → {tech → {count, kb}} aggregation
    coaches_map: dict[str, dict] = {}
    for row in rows:
        cname = row.coach_name
        if cname not in coaches_map:
            coaches_map[cname] = {"total_videos": 0, "kb_extracted": 0, "techs": {}}
        tech_entry = coaches_map[cname]["techs"].setdefault(
            row.tech_category, {"count": 0, "kb_extracted": 0}
        )
        tech_entry["count"] += row.count
        tech_entry["kb_extracted"] += int(row.kb_count or 0)
        coaches_map[cname]["total_videos"] += row.count
        coaches_map[cname]["kb_extracted"] += int(row.kb_count or 0)

    coaches: list[CoachSummaryItem] = []
    for cname, data in coaches_map.items():
        breakdown = [
            TechBreakdownItem(
                tech_category=tc,
                label=get_tech_label(tc),
                count=td["count"],
                kb_extracted=td["kb_extracted"],
            )
            for tc, td in sorted(data["techs"].items(), key=lambda x: -x[1]["count"])
        ]
        coaches.append(
            CoachSummaryItem(
                coach_name=cname,
                total_videos=data["total_videos"],
                kb_extracted=data["kb_extracted"],
                tech_breakdown=breakdown,
            )
        )

    return ok(coaches)


# ── PATCH /classifications/{id} ───────────────────────────────────────────────

@router.patch(
    "/classifications/{classification_id}",
    response_model=SuccessEnvelope[ClassificationPatchResponse],
    summary="人工修正技术分类",
)
async def patch_classification(
    classification_id: uuid.UUID,
    body: ClassificationPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[ClassificationPatchResponse]:
    """人工修正单条记录的技术分类，source 强制设为 manual，confidence=1.0."""
    if body.tech_category not in TECH_CATEGORIES:
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            message=f"invalid tech_category: {body.tech_category!r}",
            details={
                "field": "tech_category",
                "value": body.tech_category,
                "allowed": sorted(TECH_CATEGORIES),
            },
        )

    record = await session.get(CoachVideoClassification, classification_id)
    if record is None:
        raise AppException(
            ErrorCode.NOT_FOUND,
            message="classification record not found",
            details={
                "resource": "classification",
                "resource_id": str(classification_id),
            },
        )

    record.tech_category = body.tech_category
    if body.tech_tags is not None:
        record.tech_tags = body.tech_tags
    record.classification_source = "manual"
    record.confidence = 1.0

    await session.commit()
    await session.refresh(record)

    return ok(ClassificationPatchResponse.model_validate(record))
