"""Classifications router — endpoints for coach video tech classification (Feature 008).

Endpoints:
  POST /api/v1/classifications/scan          — trigger full/incremental scan
  GET  /api/v1/classifications/scan/{task_id} — query scan progress
  GET  /api/v1/classifications               — list classification records
  GET  /api/v1/classifications/summary       — coach/tech breakdown summary
  PATCH /api/v1/classifications/{id}         — manual correction
"""

from __future__ import annotations

import uuid
from typing import Optional

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.classification import (
    ClassificationItem,
    ClassificationListResponse,
    ClassificationPatchRequest,
    ClassificationPatchResponse,
    ClassificationSummaryResponse,
    CoachSummaryItem,
    ScanRequest,
    ScanStatusResponse,
    TechBreakdownItem,
)
from src.db.session import get_db
from src.models.coach_video_classification import CoachVideoClassification
from src.services.tech_classifier import TECH_CATEGORIES, get_tech_label
from src.workers.celery_app import celery_app

router = APIRouter(tags=["classifications"])


# ── POST /classifications/scan ────────────────────────────────────────────────

@router.post(
    "/classifications/scan",
    status_code=202,
    response_model=ScanStatusResponse,
    summary="触发教练视频技术分类扫描",
)
async def trigger_scan(body: ScanRequest) -> ScanStatusResponse:
    """触发全量或增量扫描，异步 Celery task，返回 task_id。"""
    if body.scan_mode not in ("full", "incremental"):
        raise HTTPException(
            status_code=400,
            detail="invalid scan_mode: must be 'full' or 'incremental'",
        )

    task_id = str(uuid.uuid4())
    from src.workers.classification_task import scan_cos_videos

    scan_cos_videos.apply_async(
        kwargs={"task_id": task_id, "scan_mode": body.scan_mode},
        task_id=task_id,  # use our task_id as Celery task ID for progress tracking
    )

    return ScanStatusResponse(
        task_id=task_id,
        status="pending",
    )


# ── GET /classifications/scan/{task_id} ───────────────────────────────────────

@router.get(
    "/classifications/scan/{task_id}",
    response_model=ScanStatusResponse,
    summary="查询扫描任务进度",
)
async def get_scan_status(task_id: str) -> ScanStatusResponse:
    """查询扫描任务状态，从 Celery result backend 获取。"""
    # Find the celery task by searching for tasks matching our task_id in meta
    # Since we use a custom task_id in the payload (not celery's own id),
    # we look for the result in the active/registered tasks.
    # The approach: we use celery's AsyncResult with the celery task id.
    # But our API task_id != celery task id. We store our task_id in the result dict.
    # So we need to scan active results — instead, we use a simpler approach:
    # store the celery task id in a Redis key when triggering.
    # For simplicity in this implementation, we use celery's inspect to find
    # the running task or check the result backend.

    # Implementation: look up via celery inspect active/reserved tasks.
    # Simpler: use the task_id as the celery task id by passing task_id explicitly.
    result = AsyncResult(task_id, app=celery_app)

    if result.state == "PENDING":
        # Task not found or still queued
        return ScanStatusResponse(task_id=task_id, status="pending")
    elif result.state == "RUNNING":
        meta = result.info or {}
        return ScanStatusResponse(
            task_id=task_id,
            status="running",
            scanned=meta.get("scanned"),
            inserted=meta.get("inserted"),
            updated=meta.get("updated"),
            skipped=meta.get("skipped"),
            errors=meta.get("errors"),
            elapsed_s=meta.get("elapsed_s"),
        )
    elif result.state == "SUCCESS":
        info = result.result or {}
        return ScanStatusResponse(
            task_id=task_id,
            status="success",
            scanned=info.get("scanned"),
            inserted=info.get("inserted"),
            updated=info.get("updated"),
            skipped=info.get("skipped"),
            errors=info.get("errors"),
            elapsed_s=info.get("elapsed_s"),
        )
    elif result.state == "FAILURE":
        return ScanStatusResponse(
            task_id=task_id,
            status="failed",
            error_detail=str(result.info) if result.info else None,
        )
    else:
        # RETRY, REVOKED, etc.
        return ScanStatusResponse(task_id=task_id, status=result.state.lower())


# ── GET /classifications ──────────────────────────────────────────────────────

@router.get(
    "/classifications",
    response_model=ClassificationListResponse,
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
) -> ClassificationListResponse:
    """按条件查询分类记录，支持分页。"""
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
    return ClassificationListResponse(total=total, items=items)


# ── GET /classifications/summary ──────────────────────────────────────────────

@router.get(
    "/classifications/summary",
    response_model=ClassificationSummaryResponse,
    summary="按教练统计技术分类汇总",
)
async def get_summary(
    coach_name: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db),
) -> ClassificationSummaryResponse:
    """按教练 + 技术类别统计视频数量和已提取知识库数量。"""
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

    coaches = []
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

    return ClassificationSummaryResponse(coaches=coaches)


# ── PATCH /classifications/{id} ───────────────────────────────────────────────

@router.patch(
    "/classifications/{classification_id}",
    response_model=ClassificationPatchResponse,
    summary="人工修正技术分类",
)
async def patch_classification(
    classification_id: uuid.UUID,
    body: ClassificationPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> ClassificationPatchResponse:
    """人工修正单条记录的技术分类，source 强制设为 manual，confidence=1.0。"""
    if body.tech_category not in TECH_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid tech_category: '{body.tech_category}' is not a valid TechCategory",
        )

    record = await session.get(CoachVideoClassification, classification_id)
    if record is None:
        raise HTTPException(status_code=404, detail="classification record not found")

    record.tech_category = body.tech_category
    if body.tech_tags is not None:
        record.tech_tags = body.tech_tags
    record.classification_source = "manual"
    record.confidence = 1.0

    await session.commit()
    await session.refresh(record)

    return ClassificationPatchResponse.model_validate(record)
