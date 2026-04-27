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
注意：``page/page_size`` 分页参数已于阶段 5 T054 统一（``limit/offset`` 已下线）。
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
    page_num: int = Query(1, ge=1, alias="page"),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[ClassificationItem]]:
    """按条件查询分类记录，支持分页。

    Feature-017 阶段 5 T054：统一使用 ``page/page_size`` 查询参数（1-based page、
    page_size 默认 20 / 最大 100）。原 ``limit/offset`` 参数已彻底下线，调用方
    需改用 ``page`` 与 ``page_size``；越界由 FastAPI 422 + VALIDATION_FAILED
    自动拦截。
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

    offset = (page_num - 1) * page_size
    stmt = stmt.order_by(CoachVideoClassification.created_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(stmt)
    records = result.scalars().all()

    items = [ClassificationItem.model_validate(r) for r in records]
    return page(items, page=page_num, page_size=page_size, total=total)


# ── GET /classifications/summary ──────────────────────────────────────────────

@router.get(
    "/classifications/summary",
    response_model=SuccessEnvelope[list[CoachSummaryItem]],
    summary="按教练统计技术分类汇总",
)
async def get_summary(
    coach_name: Optional[str] = Query(None),
    page_num: int = Query(1, ge=1, alias="page"),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[CoachSummaryItem]]:
    """按教练 + 技术类别统计视频数量和已提取知识库数量.

    Feature-017 阶段 5 T054：统一 ``page/page_size`` 分页参数（默认 20、最大 100）；
    本端点返回聚合统计（教练 × 技术类别），记录数通常较少，服务层计算完再切片。
    """
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

    return page(coaches[(page_num - 1) * page_size : page_num * page_size],
                page=page_num, page_size=page_size, total=len(coaches))


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
