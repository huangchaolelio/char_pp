"""Feature-020 · athlete_classifications router.

Endpoints:
  POST /api/v1/athlete-classifications/scan           — 触发运动员视频扫描 (US1)
  GET  /api/v1/athlete-classifications/scan/{task_id}  — 查询扫描进度
  GET  /api/v1/athlete-classifications                — 列表（分页 + 过滤）

设计要点:
- `POST /scan` 在 analysis_tasks 表创建一行 `task_type=athlete_video_classification`，
  通过 ORM before_insert 钩子自动派生 `business_phase=INFERENCE / business_step=scan_athlete_videos`
  （对齐 data-model.md § 5 + business-workflow.md）
- `task_id` 即为 analysis_tasks.id，`Celery task_id` 与之一致，便于 status 接口反查
- 状态权威来源：analysis_tasks 行（status / progress JSON）；Celery AsyncResult 仅辅助
- 列表查询走 ORM `AthleteVideoClassification`，严格物理隔离于教练侧 `CoachVideoClassification`
  （SC-006 + 章程附加约束）

本 router 所有响应走 SuccessEnvelope / AppException（章程原则 IX）。
"""

from __future__ import annotations

import uuid
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.enums import validate_enum_choice
from src.api.errors import AppException, ErrorCode
from src.api.schemas.athlete_classification import (
    AthleteClassificationItem,
    AthleteScanRequest,
    AthleteScanStatusResponse,
    AthleteScanSubmitResponse,
)
from src.api.schemas.envelope import SuccessEnvelope, ok, page
from src.db.session import get_db
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.athlete_video_classification import AthleteVideoClassification
from src.services.tech_classifier import TECH_CATEGORIES

router = APIRouter(tags=["athlete-classifications"])


# ── Service-layer helpers (exported so tests can patch them cleanly) ──────────


async def submit_scan_task(
    db: AsyncSession, scan_mode: str
) -> dict[str, Any]:
    """Create an analysis_tasks row + enqueue the Celery scan task.

    Returns ``{"task_id": UUID, "status": "pending"}``.
    """
    from src.workers.athlete_scan_task import scan_athlete_videos

    new_id = uuid.uuid4()
    row = AnalysisTask(
        id=new_id,
        task_type=TaskType.athlete_video_classification,
        # 扫描类任务无"单一视频"，填占位值满足 NOT NULL 列
        video_filename=f"athlete_scan_{scan_mode}_{new_id}",
        video_size_bytes=0,
        video_storage_uri=f"athlete-scan://{scan_mode}/{new_id}",
        status=TaskStatus.pending,
        submitted_via="batch_scan",
    )
    db.add(row)
    await db.commit()

    # Celery 任务 id 与 analysis_tasks.id 一致，便于 status 接口对齐
    scan_athlete_videos.apply_async(
        kwargs={"task_id": str(new_id), "scan_mode": scan_mode},
        task_id=str(new_id),
    )
    return {"task_id": new_id, "status": "pending"}


async def fetch_scan_task(
    db: AsyncSession, task_id: UUID
) -> AnalysisTask | None:
    """Fetch the analysis_tasks row for this athlete-scan task_id.

    Returns ``None`` when the row doesn't exist or isn't a scan task (404 trigger).
    """
    stmt = select(AnalysisTask).where(
        AnalysisTask.id == task_id,
        AnalysisTask.task_type == TaskType.athlete_video_classification,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_classifications(
    db: AsyncSession,
    *,
    page_num: int,
    page_size: int,
    athlete_id: UUID | None = None,
    athlete_name: str | None = None,
    tech_category: str | None = None,
    preprocessed: bool | None = None,
    has_diagnosis: bool | None = None,
    sort_by: str = "created_at",
    order: str = "desc",
) -> tuple[list[AthleteClassificationItem], int]:
    """List athlete classifications with filters + pagination."""
    model = AthleteVideoClassification
    stmt = select(model)
    count_stmt = select(func.count()).select_from(model)

    if athlete_id is not None:
        stmt = stmt.where(model.athlete_id == athlete_id)
        count_stmt = count_stmt.where(model.athlete_id == athlete_id)
    if athlete_name is not None:
        stmt = stmt.where(model.athlete_name == athlete_name)
        count_stmt = count_stmt.where(model.athlete_name == athlete_name)
    if tech_category is not None:
        stmt = stmt.where(model.tech_category == tech_category)
        count_stmt = count_stmt.where(model.tech_category == tech_category)
    if preprocessed is not None:
        stmt = stmt.where(model.preprocessed == preprocessed)
        count_stmt = count_stmt.where(model.preprocessed == preprocessed)
    if has_diagnosis is not None:
        if has_diagnosis:
            stmt = stmt.where(model.last_diagnosis_report_id.is_not(None))
            count_stmt = count_stmt.where(model.last_diagnosis_report_id.is_not(None))
        else:
            stmt = stmt.where(model.last_diagnosis_report_id.is_(None))
            count_stmt = count_stmt.where(model.last_diagnosis_report_id.is_(None))

    total = int((await db.execute(count_stmt)).scalar_one())

    sort_col = model.created_at if sort_by == "created_at" else model.updated_at
    stmt = stmt.order_by(sort_col.asc() if order == "asc" else sort_col.desc())
    stmt = stmt.offset((page_num - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    items = [AthleteClassificationItem.model_validate(r) for r in rows]
    return items, total


# ── POST /athlete-classifications/scan ────────────────────────────────────────


@router.post(
    "/athlete-classifications/scan",
    status_code=202,
    response_model=SuccessEnvelope[AthleteScanSubmitResponse],
    summary="触发运动员视频扫描",
)
async def trigger_athlete_scan(
    body: AthleteScanRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[AthleteScanSubmitResponse]:
    """触发全量 / 增量扫描；异步落 Celery，返回 task_id."""
    body.scan_mode = validate_enum_choice(
        body.scan_mode, field="scan_mode", allowed=["full", "incremental"],
    )
    outcome = await submit_scan_task(db, body.scan_mode)
    return ok(AthleteScanSubmitResponse(
        task_id=outcome["task_id"],
        status=outcome["status"],
    ))


# ── GET /athlete-classifications/scan/{task_id} ───────────────────────────────


@router.get(
    "/athlete-classifications/scan/{task_id}",
    response_model=SuccessEnvelope[AthleteScanStatusResponse],
    summary="查询扫描任务进度",
)
async def get_athlete_scan_status(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[AthleteScanStatusResponse]:
    """读 analysis_tasks 权威状态 + progress 快照."""
    row = await fetch_scan_task(db, task_id)
    if row is None:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"resource_id": str(task_id)},
        )

    # TaskStatus enum → 对外字符串
    status_map = {
        TaskStatus.pending: "pending",
        TaskStatus.processing: "running",
        TaskStatus.success: "success",
        TaskStatus.failed: "failed",
    }
    public_status = status_map.get(row.status, str(row.status))

    # progress JSON（由 Celery task 通过 update_state + analysis_tasks.progress 回写）
    progress = getattr(row, "progress", None) or {}
    resp = AthleteScanStatusResponse(
        task_id=row.id,
        status=public_status,
        scanned=progress.get("scanned"),
        inserted=progress.get("inserted"),
        updated=progress.get("updated"),
        skipped=progress.get("skipped"),
        errors=progress.get("errors"),
        elapsed_s=progress.get("elapsed_s"),
        error_detail=progress.get("error_detail") or row.error_message,
    )
    return ok(resp)


# ── GET /athlete-classifications ──────────────────────────────────────────────


@router.get(
    "/athlete-classifications",
    response_model=SuccessEnvelope[list[AthleteClassificationItem]],
    summary="运动员视频素材清单",
)
async def list_athlete_classifications(
    athlete_id: Optional[UUID] = Query(None),
    athlete_name: Optional[str] = Query(None),
    tech_category: Optional[str] = Query(None),
    preprocessed: Optional[bool] = Query(None),
    has_diagnosis: Optional[bool] = Query(None),
    sort_by: str = Query("created_at"),
    order: str = Query("desc"),
    page_num: int = Query(1, ge=1, alias="page"),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[AthleteClassificationItem]]:
    """按过滤条件分页查询运动员素材清单."""
    # Enum 参数校验（非法值 → 400 INVALID_ENUM_VALUE）
    if tech_category is not None:
        tech_category = validate_enum_choice(
            tech_category, field="tech_category", allowed=sorted(TECH_CATEGORIES),
        )
    sort_by = validate_enum_choice(
        sort_by, field="sort_by", allowed=["created_at", "updated_at"],
    )
    order = validate_enum_choice(
        order, field="order", allowed=["asc", "desc"],
    )

    items, total = await list_classifications(
        db,
        page_num=page_num,
        page_size=page_size,
        athlete_id=athlete_id,
        athlete_name=athlete_name,
        tech_category=tech_category,
        preprocessed=preprocessed,
        has_diagnosis=has_diagnosis,
        sort_by=sort_by,
        order=order,
    )
    return page(items, page=page_num, page_size=page_size, total=total)
