"""Tasks router — full endpoint implementations (T025–T027, T030, T036–T037, T041).

Feature-017: expert-video / athlete-video 端点已物理下线（见 _retired.py 台账），
替代为 /api/v1/tasks/classification、/api/v1/tasks/kb-extraction（原 expert-video）
与 /api/v1/tasks/diagnosis（原 athlete-video）。

Feature 012: GET /tasks list endpoint with pagination, filtering and sorting.

Feature-017（信封化）：所有成功响应用 ``ok()`` / ``page()`` 构造器包装为
``SuccessEnvelope``；所有 ``HTTPException`` 统一改为 ``AppException`` + ``ErrorCode``
枚举，由全局异常处理器序列化为错误信封（章程 v1.4.0 原则 IX）。
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

from src.utils.time_utils import now_cst
from typing import Union, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.envelope import SuccessEnvelope, ok, page as page_envelope

from src.api.enums import parse_enum_param, validate_enum_choice

from src.api.schemas.coach import TaskCoachResponse, TaskCoachUpdate
from src.api.schemas.task import (
    AudioAnalysisInfo,
    CoachingAdviceItem,
    ConflictDetail,
    CosVideoItem,
    CosVideoListResponse,
    DeviationItem,
    ExtractedTechPoint,
    MotionAnalysisItem,
    ResultSummary,
    TaskDeleteResponse,
    TaskListItemResponse,
    TaskResultAthleteResponse,
    TaskResultExpertResponse,
    TaskStatusResponse,
    TaskSubmitResponse,
    TaskSummary,
)
from src.api.schemas.teaching_tip import TeachingTipRef
from src.config import get_settings
from src.db.session import get_db
from src.models.analysis_task import AnalysisTask, BusinessPhase, TaskStatus, TaskType
from src.models.athlete_motion_analysis import AthleteMotionAnalysis
from src.models.audio_transcript import AudioTranscript
from src.models.coach import Coach
from src.models.coaching_advice import CoachingAdvice
from src.models.deviation_report import DeviationReport
from src.models.expert_tech_point import ExpertTechPoint
from src.models.teaching_tip import TeachingTip
from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase
from src.models.tech_semantic_segment import TechSemanticSegment
from src.services import cos_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tasks"])


# ── GET /tasks ───────────────────────────────────────────────────────────────

_VALID_SORT_BY = {"created_at", "completed_at"}
_VALID_ORDER = {"asc", "desc"}
# Feature-017：章程 v1.4.0 规则 1 要求 page_size ≤ 100（PaginationMeta 强约束）。
# 原 Feature-012 的 200 上限在 alembic 合入前已被章程覆盖。
_MAX_PAGE_SIZE = 100


@router.get("/tasks", response_model=SuccessEnvelope[list[TaskListItemResponse]])
async def list_tasks(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=_MAX_PAGE_SIZE, description="每页条数，最大 100（章程 v1.4.0）"),
    sort_by: str = Query("created_at", description="排序字段: created_at / completed_at"),
    order: str = Query("desc", description="排序方向: asc / desc"),
    status: Optional[str] = Query(None, description="按任务状态筛选"),
    task_type: Optional[str] = Query(None, description="按任务类型筛选: video_classification / kb_extraction / athlete_diagnosis"),
    coach_id: Optional[uuid.UUID] = Query(None, description="按教练 ID 筛选"),
    business_phase: Optional[str] = Query(None, description="Feature-018: 按业务阶段筛选 TRAINING/STANDARDIZATION/INFERENCE"),
    business_step: Optional[str] = Query(None, description="Feature-018: 按业务步骤筛选 (8 值枚举)"),
    created_after: Optional[datetime] = Query(None, description="创建时间下界（ISO 8601）"),
    created_before: Optional[datetime] = Query(None, description="创建时间上界（ISO 8601）"),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[TaskListItemResponse]]:
    """List all non-deleted tasks with pagination, filtering and sorting.

    - Default: page=1, page_size=20, sort_by=created_at, order=desc
    - page_size 由 Pydantic ``Query(le=100)`` 硬约束；越界直接 422 + VALIDATION_FAILED（章程 v1.4.0）
    - Multiple filters are combined with AND logic
    - completed_at ordering uses NULLS LAST
    """
    t_start = time.monotonic()

    # ── Parameter validation ──────────────────────────────────
    # Feature-017 阶段 5 T054：``page_size`` 由 Query ``le=100`` 预约束；
    # 原静默截断逻辑删除、成为 422 + VALIDATION_FAILED 羻伟失败。
    # Feature-017 阶段 5 T056：枚举参数统一用 parse_enum_param / validate_enum_choice
    # 帮助大小写与中划线归一化。
    sort_by = validate_enum_choice(sort_by, field="sort_by", allowed=_VALID_SORT_BY)
    order = validate_enum_choice(order, field="order", allowed=_VALID_ORDER)

    status_enum: Optional[TaskStatus] = (
        parse_enum_param(status, field="status", enum_cls=TaskStatus)
        if status is not None else None
    )

    task_type_enum: Optional[TaskType] = (
        parse_enum_param(task_type, field="task_type", enum_cls=TaskType)
        if task_type is not None else None
    )

    # Feature-018: business_phase / business_step 校验 + 组合矛盾拦截
    from src.api.phase_params import parse_business_phase
    phase_enum: Optional[BusinessPhase] = parse_business_phase(business_phase, field="business_phase")
    _VALID_BUSINESS_STEPS = {
        "scan_cos_videos", "preprocess_video", "classify_video", "extract_kb",
        "review_conflicts", "kb_version_activate", "build_standards", "diagnose_athlete",
    }
    step_val: Optional[str] = (
        validate_enum_choice(business_step, field="business_step", allowed=_VALID_BUSINESS_STEPS)
        if business_step is not None else None
    )
    # (phase, step, task_type) 三元组语义矛盾 ⇒ 400 INVALID_PHASE_STEP_COMBO
    from src.services.business_workflow_service import _validate_phase_step_task_type_combo
    _validate_phase_step_task_type_combo(
        phase_enum.value if phase_enum else None,
        step_val,
        task_type_enum.value if task_type_enum else None,
    )

    # ── Build base query ────────────────────────────────────────────────────
    base_stmt = (        select(AnalysisTask, Coach.name.label("coach_name"))
        .outerjoin(Coach, AnalysisTask.coach_id == Coach.id)
        .where(AnalysisTask.deleted_at.is_(None))
    )

    # ── Apply filters ─────────────────────────────────────────────────────────
    if status_enum is not None:
        base_stmt = base_stmt.where(AnalysisTask.status == status_enum)
    if task_type_enum is not None:
        base_stmt = base_stmt.where(AnalysisTask.task_type == task_type_enum)
    if coach_id is not None:
        base_stmt = base_stmt.where(AnalysisTask.coach_id == coach_id)
    if phase_enum is not None:
        base_stmt = base_stmt.where(AnalysisTask.business_phase == phase_enum)
    if step_val is not None:
        base_stmt = base_stmt.where(AnalysisTask.business_step == step_val)
    if created_after is not None:
        base_stmt = base_stmt.where(AnalysisTask.created_at >= created_after)
    if created_before is not None:
        base_stmt = base_stmt.where(AnalysisTask.created_at <= created_before)

    # ── Count total ───────────────────────────────────────────────────────────
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # ── Apply sorting ─────────────────────────────────────────────────────────
    if sort_by == "completed_at":
        sort_col = AnalysisTask.completed_at
        if order == "desc":
            base_stmt = base_stmt.order_by(sort_col.desc().nullslast())
        else:
            base_stmt = base_stmt.order_by(sort_col.asc().nullslast())
    else:  # created_at
        sort_col = AnalysisTask.created_at
        if order == "desc":
            base_stmt = base_stmt.order_by(sort_col.desc())
        else:
            base_stmt = base_stmt.order_by(sort_col.asc())

    # ── Apply pagination ──────────────────────────────────────────────────────
    offset = (page - 1) * page_size
    base_stmt = base_stmt.offset(offset).limit(page_size)

    rows_result = await db.execute(base_stmt)
    rows = rows_result.all()

    # ── Build response items ──────────────────────────────────────────────────
    items = [
        TaskListItemResponse(
            task_id=task.id,
            task_type=task.task_type.value,
            status=task.status.value,
            video_filename=task.video_filename,
            video_storage_uri=task.video_storage_uri,
            video_duration_seconds=task.video_duration_seconds,
            video_size_bytes=task.video_size_bytes,
            video_fps=task.video_fps,
            video_resolution=task.video_resolution,
            execution_seconds=(
                (task.completed_at - task.started_at).total_seconds()
                if task.completed_at and task.started_at else None
            ),
            timing_stats=task.timing_stats,
            progress_pct=task.progress_pct,
            error_message=task.error_message,
            knowledge_base_version=task.knowledge_base_version,
            coach_id=task.coach_id,
            coach_name=coach_name,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )
        for task, coach_name in rows
    ]

    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    logger.info(
        "task_list_query total=%d page=%d page_size=%d elapsed_ms=%d",
        total, page, page_size, elapsed_ms,
    )

    # Feature-017：统一使用 page_envelope 构造器（meta = {page, page_size, total}）。
    # 原 Feature-012 的 total_pages 字段已下线——章程 v1.4.0 信封规范 PaginationMeta
    # 仅保留 page/page_size/total 三项，前端按 ceil(total/page_size) 自算总页数。
    return page_envelope(items, page=page, page_size=page_size, total=total)

# ── GET /tasks/cos-videos ────────────────────────────────────────────────────

@router.get("/tasks/cos-videos", response_model=SuccessEnvelope[CosVideoListResponse])
def list_cos_videos(
    action_type: str = "all",
) -> SuccessEnvelope[CosVideoListResponse]:
    """List available COS videos filtered by action type.

    Query params:
        action_type: "forehand" | "backhand" | "all" (default: "all")

    Returns video list with cos_object_key ready to submit to POST /api/v1/tasks/kb-extraction
    （Feature-017: 原 /tasks/expert-video 已下线）.
    """
    action_type = validate_enum_choice(
        action_type, field="action_type", allowed=["forehand", "backhand", "all"],
    )
    videos = cos_client.list_videos(action_type=action_type)
    return ok(CosVideoListResponse(
        action_type_filter=action_type,
        total=len(videos),
        videos=[CosVideoItem(**v) for v in videos],
    ))


# ── GET /tasks/{task_id} ─────────────────────────────────────────────────────

@router.get("/tasks/{task_id}", response_model=SuccessEnvelope[TaskStatusResponse])
async def get_task_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[TaskStatusResponse]:
    """Return current status and metadata for a task.

    Returns 404 if the task does not exist or has been soft-deleted.
    """
    # Validate UUID format before hitting the DB
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"task_id": task_id},
        )

    result = await db.execute(
        select(AnalysisTask)
        .options(selectinload(AnalysisTask.coach))
        .where(
            AnalysisTask.id == task_uuid,
            AnalysisTask.deleted_at.is_(None),
        )
    )
    task = result.scalar_one_or_none()

    if task is None:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"task_id": task_id},
        )

    # ── Feature 012: aggregate related entity counts ──────────────────────────
    tech_point_count_result = await db.execute(
        select(func.count()).where(ExpertTechPoint.source_video_id == task_uuid)
    )
    tech_point_count = tech_point_count_result.scalar_one()

    has_transcript_result = await db.execute(
        select(func.count()).where(AudioTranscript.task_id == task_uuid)
    )
    has_transcript = has_transcript_result.scalar_one() > 0

    semantic_segment_count_result = await db.execute(
        select(func.count()).where(TechSemanticSegment.task_id == task_uuid)
    )
    semantic_segment_count = semantic_segment_count_result.scalar_one()

    motion_analysis_count_result = await db.execute(
        select(func.count()).where(AthleteMotionAnalysis.task_id == task_uuid)
    )
    motion_analysis_count = motion_analysis_count_result.scalar_one()

    # deviation_count: subquery via athlete_motion_analyses
    motion_ids_stmt = select(AthleteMotionAnalysis.id).where(
        AthleteMotionAnalysis.task_id == task_uuid
    )
    deviation_count_result = await db.execute(
        select(func.count()).where(DeviationReport.analysis_id.in_(motion_ids_stmt))
    )
    deviation_count = deviation_count_result.scalar_one()

    advice_count_result = await db.execute(
        select(func.count()).where(CoachingAdvice.task_id == task_uuid)
    )
    advice_count = advice_count_result.scalar_one()

    summary = TaskSummary(
        tech_point_count=tech_point_count,
        has_transcript=has_transcript,
        semantic_segment_count=semantic_segment_count,
        motion_analysis_count=motion_analysis_count,
        deviation_count=deviation_count,
        advice_count=advice_count,
    )

    return ok(TaskStatusResponse(
        task_id=task.id,
        task_type=task.task_type.value,
        status=task.status.value,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        video_duration_seconds=task.video_duration_seconds,
        video_size_bytes=task.video_size_bytes,
        video_fps=task.video_fps,
        video_resolution=task.video_resolution,
        execution_seconds=(
            (task.completed_at - task.started_at).total_seconds()
            if task.completed_at and task.started_at else None
        ),
        progress_pct=task.progress_pct,
        processed_segments=task.processed_segments,
        total_segments=task.total_segments,
        audio_fallback_reason=task.audio_fallback_reason,
        knowledge_base_version=task.knowledge_base_version,
        # Feature 006: coach info via relationship
        coach_id=task.coach_id,
        coach_name=task.coach.name if task.coach else None,
        # Feature 007: processing timing stats
        timing_stats=task.timing_stats,
        # Feature 012: related entity summary
        summary=summary,
    ))


# ── GET /tasks/{task_id}/result ──────────────────────────────────────────────

@router.get(
    "/tasks/{task_id}/result",
    response_model=SuccessEnvelope[Union[TaskResultExpertResponse, TaskResultAthleteResponse]],
)
async def get_task_result(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[Union[TaskResultExpertResponse, TaskResultAthleteResponse]]:
    """Return the full analysis result for a completed task.

    - kb_extraction: returns KB draft version + extracted tech points list.
    - athlete_diagnosis: returns motion analyses with deviation reports and coaching advice.

    Returns 404 if the task does not exist or has been soft-deleted.
    Returns 400 ``TASK_NOT_READY`` if the task has not yet reached status=success
    (Feature-017 章程对齐：状态校验类错误统一 400，原 409 下线).
    """
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"task_id": task_id},
        )

    result = await db.execute(
        select(AnalysisTask).where(
            AnalysisTask.id == task_uuid,
            AnalysisTask.deleted_at.is_(None),
        )
    )
    task = result.scalar_one_or_none()

    if task is None:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"task_id": task_id},
        )

    if task.status != TaskStatus.success:
        raise AppException(
            ErrorCode.TASK_NOT_READY,
            message=f"任务尚未完成，当前状态: {task.status.value}",
            details={"task_id": task_id, "status": task.status.value},
        )

    # ── kb_extraction branch ─────────────────────────────────────────────────
    if task.task_type == TaskType.kb_extraction:
        # Load all tech points with their source segment timestamps (FR-008)
        points_result = await db.execute(
            select(ExpertTechPoint, TechSemanticSegment)
            .outerjoin(
                TechSemanticSegment,
                ExpertTechPoint.transcript_segment_id == TechSemanticSegment.id,
            )
            .where(
                ExpertTechPoint.source_video_id == task_uuid,
                ExpertTechPoint.knowledge_base_version == task.knowledge_base_version,
            )
        )
        points = points_result.all()

        # Determine whether the KB version is still pending approval (draft)
        kb_result = await db.execute(
            select(TechKnowledgeBase).where(
                TechKnowledgeBase.version == task.knowledge_base_version
            )
        )
        kb = kb_result.scalar_one_or_none()
        pending_approval = (kb is not None and kb.status == KBStatus.draft)

        extracted = [
            ExtractedTechPoint(
                action_type=p.action_type.value,
                dimension=p.dimension,
                param_min=p.param_min,
                param_max=p.param_max,
                param_ideal=p.param_ideal,
                unit=p.unit,
                extraction_confidence=p.extraction_confidence,
                source_type=p.source_type,
                conflict_flag=p.conflict_flag,
                conflict_detail=p.conflict_detail,
                segment_start_ms=seg.start_ms if seg is not None else None,
                segment_end_ms=seg.end_ms if seg is not None else None,
            )
            for p, seg in points
        ]

        # Feature 002: query AudioTranscript for audio analysis summary
        audio_info: Optional[AudioAnalysisInfo] = None
        at_result = await db.execute(
            select(AudioTranscript).where(AudioTranscript.task_id == task_uuid)
        )
        at = at_result.scalar_one_or_none()
        if at is not None:
            audio_info = AudioAnalysisInfo(
                enabled=True,
                quality_flag=at.quality_flag.value if at.quality_flag else None,
                fallback_reason=at.fallback_reason,
                transcript_sentence_count=len(at.sentences) if at.sentences else 0,
            )
        elif task.audio_fallback_reason:
            audio_info = AudioAnalysisInfo(
                enabled=True,
                quality_flag=None,
                fallback_reason=task.audio_fallback_reason,
                transcript_sentence_count=None,
            )

        # Feature 002: collect conflict details
        conflicts = [
            ConflictDetail(
                dimension=p.dimension,
                visual_ideal=p.conflict_detail["visual"]["param_ideal"],
                audio_ideal=p.conflict_detail["audio"]["param_ideal"],
                diff_pct=p.conflict_detail["diff_pct"],
            )
            for p, seg in points
            if p.conflict_flag and p.conflict_detail
        ]

        return ok(TaskResultExpertResponse(
            task_id=task.id,
            knowledge_base_version_draft=task.knowledge_base_version,
            extracted_points_count=len(extracted),
            extracted_points=extracted,
            pending_approval=pending_approval,
            audio_analysis=audio_info,
            conflicts=conflicts,
        ))

    # ── athlete_diagnosis branch ──────────────────────────────────────────────
    analyses_result = await db.execute(
        select(AthleteMotionAnalysis).where(
            AthleteMotionAnalysis.task_id == task_uuid
        ).order_by(AthleteMotionAnalysis.segment_start_ms)
    )
    analyses = analyses_result.scalars().all()

    # Feature 005: pre-load teaching tips keyed by action_type for fast lookup
    settings = get_settings()
    tips_result = await db.execute(
        select(TeachingTip).order_by(
            TeachingTip.source_type.desc(),  # 'human' > 'auto'
            TeachingTip.confidence.desc(),
        )
    )
    all_teaching_tips = tips_result.scalars().all()

    def _get_tips_for_action(action_type_val: str) -> list[TeachingTipRef]:
        matched = [t for t in all_teaching_tips if t.action_type == action_type_val]
        return [
            TeachingTipRef(
                tip_text=t.tip_text,
                tech_phase=t.tech_phase,
                source_type=t.source_type,
            )
            for t in matched[: settings.max_teaching_tips]
        ]

    # Build response for each motion analysis
    motion_analysis_items: list[MotionAnalysisItem] = []
    total_deviations = 0
    stable_deviations = 0
    low_confidence_count = 0
    best_advice_dim: Optional[str] = None
    best_impact = -1.0

    for analysis in analyses:
        if analysis.is_low_confidence:
            low_confidence_count += 1

        # Load deviation reports
        dr_result = await db.execute(
            select(DeviationReport).where(
                DeviationReport.analysis_id == analysis.id
            ).order_by(DeviationReport.impact_score.desc().nullslast())
        )
        reports = dr_result.scalars().all()
        total_deviations += len(reports)
        stable_deviations += sum(
            1 for r in reports if r.is_stable_deviation is True
        )

        deviation_items = [
            DeviationItem(
                deviation_id=r.id,
                dimension=r.dimension,
                measured_value=r.measured_value,
                ideal_value=r.ideal_value,
                deviation_value=r.deviation_value,
                deviation_direction=r.deviation_direction.value,
                confidence=r.confidence,
                is_low_confidence=r.is_low_confidence,
                is_stable_deviation=r.is_stable_deviation,
                impact_score=r.impact_score,
            )
            for r in reports
        ]

        # Load coaching advice for this analysis (via task_id + deviation_ids)
        deviation_ids = [r.id for r in reports]
        if deviation_ids:
            ca_result = await db.execute(
                select(CoachingAdvice).where(
                    CoachingAdvice.task_id == task_uuid,
                    CoachingAdvice.deviation_id.in_(deviation_ids),
                ).order_by(CoachingAdvice.impact_score.desc())
            )
            advice_list = ca_result.scalars().all()
        else:
            advice_list = []

        advice_items = [
            CoachingAdviceItem(
                advice_id=a.id,
                dimension=next(
                    (r.dimension for r in reports if r.id == a.deviation_id),
                    "unknown",
                ),
                deviation_description=a.deviation_description,
                improvement_target=a.improvement_target,
                improvement_method=a.improvement_method,
                impact_score=a.impact_score,
                reliability_level=a.reliability_level.value,
                reliability_note=a.reliability_note,
                teaching_tips=_get_tips_for_action(analysis.action_type.value),
            )
            for a in advice_list
        ]

        # Track top advice dimension
        for a in advice_list:
            if a.impact_score > best_impact:
                best_impact = a.impact_score
                best_advice_dim = next(
                    (r.dimension for r in reports if r.id == a.deviation_id),
                    None,
                )

        motion_analysis_items.append(
            MotionAnalysisItem(
                analysis_id=analysis.id,
                action_type=analysis.action_type.value,
                segment_start_ms=analysis.segment_start_ms,
                segment_end_ms=analysis.segment_end_ms,
                overall_confidence=analysis.overall_confidence,
                is_low_confidence=analysis.is_low_confidence,
                deviation_report=deviation_items,
                coaching_advice=advice_items,
            )
        )

    analyzed_count = sum(
        1 for a in analyses if a.action_type.value != "unknown"
    )

    summary = ResultSummary(
        total_actions_detected=len(analyses),
        actions_analyzed=analyzed_count,
        actions_low_confidence=low_confidence_count,
        total_deviations=total_deviations,
        stable_deviations=stable_deviations,
        top_advice_dimension=best_advice_dim,
    )

    return ok(TaskResultAthleteResponse(
        task_id=task.id,
        knowledge_base_version=task.knowledge_base_version or "",
        motion_analyses=motion_analysis_items,
        summary=summary,
    ))


# ── DELETE /tasks/{task_id} ──────────────────────────────────────────────────

@router.delete("/tasks/{task_id}", response_model=SuccessEnvelope[TaskDeleteResponse])
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[TaskDeleteResponse]:
    """Soft-delete a task and all its associated data.

    Sets deleted_at to now; physical cleanup runs on a daily schedule.
    Returns 404 if the task does not exist or is already deleted.
    """
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"task_id": task_id},
        )

    result = await db.execute(
        select(AnalysisTask).where(
            AnalysisTask.id == task_uuid,
            AnalysisTask.deleted_at.is_(None),
        )
    )
    task = result.scalar_one_or_none()

    if task is None:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"task_id": task_id},
        )

    now = now_cst()
    task.deleted_at = now
    await db.commit()

    return ok(TaskDeleteResponse(
        task_id=task.id,
        deleted_at=now,
        message="任务及关联数据已标记删除，将在 24 小时内物理清除",
    ))


# ═════════════════════════════════════════════════════════════════════════════
# Feature 013 — Task pipeline redesign (US1, US2)
#
# Three single-submit endpoints + three batch-submit endpoints exposing the
# three isolated task channels. All go through TaskSubmissionService which
# enforces DB-authoritative capacity + partial-unique idempotency index.
# ═════════════════════════════════════════════════════════════════════════════

from src.api.schemas.task_submit import (  # noqa: E402
    ChannelSnapshot as _F13ChannelSnapshot,
    ClassificationBatchRequest as _F13ClassificationBatchRequest,
    ClassificationSingleRequest as _F13ClassificationSingleRequest,
    DiagnosisBatchRequest as _F13DiagnosisBatchRequest,
    DiagnosisSingleRequest as _F13DiagnosisSingleRequest,
    KbExtractionBatchRequest as _F13KbExtractionBatchRequest,
    KbExtractionSingleRequest as _F13KbExtractionSingleRequest,
    SubmissionItem as _F13SubmissionItem,
    SubmissionResult as _F13SubmissionResult,
)
from src.models.analysis_task import TaskType as _F13TaskType  # noqa: E402
from src.services.classification_gate_service import (  # noqa: E402
    ClassificationGateService as _F13ClassificationGateService,
)
from src.services.task_submission_service import (  # noqa: E402
    BatchTooLargeError as _F13BatchTooLargeError,
    ChannelDisabledError as _F13ChannelDisabledError,
    SubmissionInputItem as _F13SubmissionInputItem,
    TaskSubmissionService as _F13TaskSubmissionService,
)


def _f13_serialise_result(result) -> _F13SubmissionResult:
    """Map service-layer dataclasses → Pydantic response schema."""
    from pathlib import PurePosixPath

    items: list[_F13SubmissionItem] = []
    for o in result.items:
        items.append(
            _F13SubmissionItem(
                index=o.index,
                accepted=o.accepted,
                task_id=o.task_id,
                cos_object_key=o.cos_object_key,
                rejection_code=o.rejection_code,
                rejection_message=o.rejection_message,
                existing_task_id=o.existing_task_id,
            )
        )
    snap = _F13ChannelSnapshot(
        task_type=result.channel.task_type.value,
        queue_capacity=result.channel.queue_capacity,
        concurrency=result.channel.concurrency,
        current_pending=result.channel.current_pending,
        current_processing=result.channel.current_processing,
        remaining_slots=result.channel.remaining_slots,
        enabled=result.channel.enabled,
        recent_completion_rate_per_min=result.channel.recent_completion_rate_per_min,
    )
    _ = PurePosixPath  # unused import placeholder to avoid lint churn
    return _F13SubmissionResult(
        task_type=result.task_type.value,
        accepted=result.accepted,
        rejected=result.rejected,
        items=items,
        channel=snap,
        submitted_at=result.submitted_at,
    )


def _f13_submission_from_classification_req(
    body: _F13ClassificationSingleRequest,
) -> _F13SubmissionInputItem:
    return _F13SubmissionInputItem(
        cos_object_key=body.cos_object_key,
        task_kwargs={},
        video_filename=body.cos_object_key.rsplit("/", 1)[-1],
        video_storage_uri=body.cos_object_key,
        force=body.force,
    )


def _f13_submission_from_kb_req(
    body: _F13KbExtractionSingleRequest,
) -> _F13SubmissionInputItem:
    return _F13SubmissionInputItem(
        cos_object_key=body.cos_object_key,
        task_kwargs={
            "enable_audio_analysis": body.enable_audio_analysis,
            "audio_language": body.audio_language,
        },
        video_filename=body.cos_object_key.rsplit("/", 1)[-1],
        video_storage_uri=body.cos_object_key,
        force=body.force,
    )


def _f13_submission_from_diagnosis_req(
    body: _F13DiagnosisSingleRequest,
) -> _F13SubmissionInputItem:
    return _F13SubmissionInputItem(
        cos_object_key=None,
        task_kwargs={"knowledge_base_version": body.knowledge_base_version},
        video_filename=body.video_storage_uri.rsplit("/", 1)[-1],
        video_storage_uri=body.video_storage_uri,
        knowledge_base_version=body.knowledge_base_version,
        force=body.force,
    )


async def _f13_submit(
    db: AsyncSession,
    task_type: _F13TaskType,
    items: list[_F13SubmissionInputItem],
    submitted_via: str,
) -> _F13SubmissionResult:
    svc = _F13TaskSubmissionService()
    try:
        result = await svc.submit_batch(
            session=db, task_type=task_type, items=items, submitted_via=submitted_via,
        )
    except _F13BatchTooLargeError as exc:
        raise AppException(
            ErrorCode.BATCH_TOO_LARGE, message=str(exc),
        ) from exc
    except _F13ChannelDisabledError as exc:
        raise AppException(
            ErrorCode.CHANNEL_DISABLED, message=str(exc),
        ) from exc
    except ValueError as exc:
        raise AppException(
            ErrorCode.INVALID_INPUT, message=str(exc),
        ) from exc
    return _f13_serialise_result(result)


# ── PATCH /tasks/{task_id}/coach ──────────────────────────────────────────────
# Feature-017 阶段 5 T050：从 coaches.py 搬迁到 tasks.py（资源归属为 task，业务逻辑不变）

@router.patch("/tasks/{task_id}/coach", response_model=SuccessEnvelope[TaskCoachResponse])
async def assign_coach_to_task(
    task_id: uuid.UUID,
    body: TaskCoachUpdate,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[TaskCoachResponse]:
    """Assign (or remove) a coach for an expert video task."""
    task_result = await db.execute(
        select(AnalysisTask).where(
            AnalysisTask.id == task_id,
            AnalysisTask.deleted_at.is_(None),
        )
    )
    task = task_result.scalar_one_or_none()
    if task is None:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"task_id": str(task_id)},
        )

    coach_name: str | None = None
    if body.coach_id is not None:
        coach_result = await db.execute(
            select(Coach).where(Coach.id == body.coach_id)
        )
        coach = coach_result.scalar_one_or_none()
        if coach is None:
            raise AppException(
                ErrorCode.COACH_NOT_FOUND,
                details={"coach_id": str(body.coach_id)},
            )
        if not coach.is_active:
            raise AppException(
                ErrorCode.COACH_INACTIVE,
                message="无法关联已停用的教练",
                details={"coach_id": str(body.coach_id)},
            )
        coach_name = coach.name

    task.coach_id = body.coach_id
    await db.commit()
    logger.info(
        "task coach assigned task_id=%s coach_id=%s", task_id, body.coach_id
    )
    return ok(TaskCoachResponse(
        task_id=task_id,
        coach_id=body.coach_id,
        coach_name=coach_name,
    ))


# ── POST /tasks/classification (single) ──────────────────────────
@router.post(
    "/tasks/classification",
    response_model=SuccessEnvelope[_F13SubmissionResult],
    status_code=200,
    summary="Submit a single coach video for tech_category classification",
)
async def submit_classification(
    body: _F13ClassificationSingleRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[_F13SubmissionResult]:
    item = _f13_submission_from_classification_req(body)
    result = await _f13_submit(
        db, _F13TaskType.video_classification, [item], submitted_via="single"
    )
    return ok(result)

# ── POST /tasks/kb-extraction (single) ───────────────────────────
@router.post(
    "/tasks/kb-extraction",
    response_model=SuccessEnvelope[_F13SubmissionResult],
    status_code=200,
    summary="Submit a single classified video for knowledge-base extraction",
)
async def submit_kb_extraction(
    body: _F13KbExtractionSingleRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[_F13SubmissionResult]:
    # FR-004a: pre-check that the video has a non-'unclassified' tech_category.
    gate = _F13ClassificationGateService()
    if not await gate.check_classified(db, body.cos_object_key):
        current = await gate.get_tech_category(db, body.cos_object_key)
        raise AppException(
            ErrorCode.CLASSIFICATION_REQUIRED,
            message=(
                "video must be classified before kb-extraction "
                f"(current tech_category={current!r})"
            ),
            details={
                "cos_object_key": body.cos_object_key,
                "current_tech_category": current,
            },
        )

    # Feature 014: carry tech_category + force into task_kwargs so the
    # submission service can seed the ExtractionJob + 6 PipelineSteps in the
    # same DB transaction as the analysis_tasks INSERT.
    tech_category = await gate.get_tech_category(db, body.cos_object_key)
    item = _f13_submission_from_kb_req(body)
    item.task_kwargs = {
        **item.task_kwargs,
        "tech_category": tech_category or "unclassified",
        "force": body.force,
    }
    result = await _f13_submit(
        db, _F13TaskType.kb_extraction, [item], submitted_via="single"
    )
    return ok(result)

# ── POST /tasks/diagnosis (single) ──────────────────────────────
@router.post(
    "/tasks/diagnosis",
    response_model=SuccessEnvelope[_F13SubmissionResult],
    status_code=200,
    summary="Submit a single athlete video for motion diagnosis",
)
async def submit_diagnosis(
    body: _F13DiagnosisSingleRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[_F13SubmissionResult]:
    item = _f13_submission_from_diagnosis_req(body)
    result = await _f13_submit(
        db, _F13TaskType.athlete_diagnosis, [item], submitted_via="single"
    )
    return ok(result)

# ══════════════════════════════════════════════════════════════════════════════
# Feature 013 — US2: Batch submission endpoints
# ══════════════════════════════════════════════════════════════════════════════


# ── POST /tasks/classification/batch ─────────────────────────────
@router.post(
    "/tasks/classification/batch",
    response_model=SuccessEnvelope[_F13SubmissionResult],
    status_code=200,
    summary="Batch-submit coach videos for tech_category classification",
)
async def submit_classification_batch(
    body: _F13ClassificationBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[_F13SubmissionResult]:
    items = [_f13_submission_from_classification_req(i) for i in body.items]
    result = await _f13_submit(
        db, _F13TaskType.video_classification, items, submitted_via="batch"
    )
    return ok(result)

# ── POST /tasks/kb-extraction/batch ────────────────────────────
@router.post(
    "/tasks/kb-extraction/batch",
    response_model=SuccessEnvelope[_F13SubmissionResult],
    status_code=200,
    summary="Batch-submit classified videos for knowledge-base extraction",
)
async def submit_kb_extraction_batch(
    body: _F13KbExtractionBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[_F13SubmissionResult]:
    # FR-004a batch variant: per-item pre-gate. Unclassified items are rejected
    # up-front with CLASSIFICATION_REQUIRED; the rest flow to the service.
    # Batch-size guard runs first so 101-item payloads short-circuit before
    # any gate I/O (keeps 400 BATCH_TOO_LARGE semantics clean).
    from src.config import get_settings as _get_settings
    settings = _get_settings()
    if len(body.items) > settings.batch_max_size:
        raise AppException(
            ErrorCode.BATCH_TOO_LARGE,
            message=(
                f"batch size {len(body.items)} exceeds max "
                f"{settings.batch_max_size}"
            ),
            details={
                "submitted_count": len(body.items),
                "max_allowed": settings.batch_max_size,
            },
        )
    gate = _F13ClassificationGateService()
    classified_items: list[_F13SubmissionInputItem] = []
    classified_original_index: list[int] = []
    gate_rejections: list[_F13SubmissionItem] = []
    for idx, req in enumerate(body.items):
        if await gate.check_classified(db, req.cos_object_key):
            item = _f13_submission_from_kb_req(req)
            # Feature 014: inject tech_category + force so submit_batch can
            # seed ExtractionJob rows in the same transaction.
            item.task_kwargs = {
                **item.task_kwargs,
                "tech_category": (
                    await gate.get_tech_category(db, req.cos_object_key)
                ) or "unclassified",
                "force": getattr(req, "force", False),
            }
            classified_items.append(item)
            classified_original_index.append(idx)
        else:
            current = await gate.get_tech_category(db, req.cos_object_key)
            gate_rejections.append(
                _F13SubmissionItem(
                    index=idx,
                    accepted=False,
                    task_id=None,
                    cos_object_key=req.cos_object_key,
                    rejection_code="CLASSIFICATION_REQUIRED",
                    rejection_message=(
                        "video must be classified before kb-extraction "
                        f"(current tech_category={current!r})"
                    ),
                    existing_task_id=None,
                )
            )

    # If every item failed the gate, still return a 200 with live channel snapshot.
    if not classified_items:
        svc = _F13TaskSubmissionService()
        snap = await svc._channels.get_snapshot(db, _F13TaskType.kb_extraction)
        return ok(_F13SubmissionResult(
            task_type=_F13TaskType.kb_extraction.value,
            accepted=0,
            rejected=len(gate_rejections),
            items=gate_rejections,
            channel=_F13ChannelSnapshot(
                task_type=snap.task_type.value,
                queue_capacity=snap.queue_capacity,
                concurrency=snap.concurrency,
                current_pending=snap.current_pending,
                current_processing=snap.current_processing,
                remaining_slots=snap.remaining_slots,
                enabled=snap.enabled,
                recent_completion_rate_per_min=snap.recent_completion_rate_per_min,
            ),
        submitted_at=now_cst(),
        ))

    service_result = await _f13_submit(
        db, _F13TaskType.kb_extraction, classified_items, submitted_via="batch"
    )

    # Remap service-item indices back to the original request positions and
    # merge with the gate rejections — preserving original order.
    merged: list[_F13SubmissionItem] = []
    for svc_item in service_result.items:
        merged.append(
            svc_item.model_copy(
                update={"index": classified_original_index[svc_item.index]}
            )
        )
    merged.extend(gate_rejections)
    merged.sort(key=lambda it: it.index)

    return ok(service_result.model_copy(
        update={
            "accepted": service_result.accepted,
            "rejected": service_result.rejected + len(gate_rejections),
            "items": merged,
        }
    ))


# ── POST /tasks/diagnosis/batch ────────────────────────────────
@router.post(
    "/tasks/diagnosis/batch",
    response_model=SuccessEnvelope[_F13SubmissionResult],
    status_code=200,
    summary="Batch-submit athlete videos for motion diagnosis",
)
async def submit_diagnosis_batch(
    body: _F13DiagnosisBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[_F13SubmissionResult]:
    items = [_f13_submission_from_diagnosis_req(i) for i in body.items]
    result = await _f13_submit(
        db, _F13TaskType.athlete_diagnosis, items, submitted_via="batch"
    )
    return ok(result)

# ═════════════════════════════════════════════════════════════════════════════
# Feature 016 — Video preprocessing pipeline
#
# ``POST /tasks/preprocessing``      — single-video preprocessing submission
# ``POST /tasks/preprocessing/batch``— batch submission with per-item isolation
#
# Uses its own service layer (preprocessing_service) because the
# ``video_preprocessing_jobs`` table is separate from ``analysis_tasks``. The
# preprocessing channel is a 5th TaskType (Feature-013 channel model reused).
# ═════════════════════════════════════════════════════════════════════════════

from src.api.schemas.preprocessing import (  # noqa: E402
    PreprocessingBatchItemResult as _PrepBatchItem,
    PreprocessingBatchSubmitRequest as _PrepBatchRequest,
    PreprocessingBatchSubmitResponse as _PrepBatchResponse,
    PreprocessingSubmitRequest as _PrepRequest,
    PreprocessingSubmitResponse as _PrepResponse,
)
from src.services import preprocessing_service as _preprocessing_service  # noqa: E402


def _preprocessing_enqueue_task(job_id) -> None:
    """Dispatch a Celery task so the worker picks it up."""
    from src.workers.preprocessing_task import preprocess_video as _task
    _task.delay(str(job_id))


@router.post(
    "/tasks/preprocessing",
    response_model=SuccessEnvelope[_PrepResponse],
    status_code=200,
    summary="Submit a single coach video for preprocessing",
)
async def submit_preprocessing(
    body: _PrepRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[_PrepResponse]:
    try:
        outcome = await _preprocessing_service.create_or_reuse(
            db,
            cos_object_key=body.cos_object_key,
            force=body.force,
            idempotency_key=body.idempotency_key,
        )
    except _preprocessing_service.CosKeyNotClassifiedError as exc:
        raise AppException(
            ErrorCode.COS_KEY_NOT_CLASSIFIED, message=str(exc),
        ) from exc
    except _preprocessing_service.ChannelQueueFullError as exc:
        # Feature-017: 通道满 → 503（章程 v1.4.0 / error-codes.md）
        raise AppException(
            ErrorCode.CHANNEL_QUEUE_FULL, message=str(exc),
        ) from exc

    await db.commit()
    if not outcome.reused:
        _preprocessing_enqueue_task(outcome.job_id)

    return ok(_PrepResponse(
        job_id=outcome.job_id,
        status=outcome.status,
        reused=outcome.reused,
        cos_object_key=outcome.cos_object_key,
        segment_count=outcome.segment_count,
        has_audio=outcome.has_audio,
        started_at=outcome.started_at,
        completed_at=outcome.completed_at,
    ))


@router.post(
    "/tasks/preprocessing/batch",
    response_model=SuccessEnvelope[_PrepBatchResponse],
    status_code=200,
    summary="Batch-submit coach videos for preprocessing",
)
async def submit_preprocessing_batch(
    body: _PrepBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[_PrepBatchResponse]:
    items = [(it.cos_object_key, it.force) for it in body.items]
    try:
        results = await _preprocessing_service.create_or_reuse_batch(
            db, items=items,
        )
    except _preprocessing_service.BatchTooLargeError as exc:
        raise AppException(
            ErrorCode.BATCH_TOO_LARGE, message=str(exc),
        ) from exc

    await db.commit()

    # Enqueue only fresh (non-reused, non-error) jobs.
    for item in results:
        if item.job_id is not None and not item.reused and item.error_code is None:
            _preprocessing_enqueue_task(item.job_id)

    submitted = sum(1 for r in results if r.error_code is None)
    reused = sum(1 for r in results if r.reused)
    failed = sum(1 for r in results if r.error_code is not None)

    return ok(_PrepBatchResponse(
        submitted=submitted,
        reused=reused,
        failed=failed,
        results=[
            _PrepBatchItem(
                cos_object_key=r.cos_object_key,
                job_id=r.job_id,
                status=r.status,
                reused=r.reused,
                error_code=r.error_code,
                error_message=r.error_message,
            )
            for r in results
        ],
    ))
