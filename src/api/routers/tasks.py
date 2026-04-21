"""Tasks router — full endpoint implementations (T025–T027, T030, T036–T037, T041).

US1 endpoints: expert-video submission, task status, expert result, soft-delete.
US2 endpoints: athlete-video submission, athlete result (with deviation reports).
US3 update: athlete result includes coaching_advice populated.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone as _tz
UTC = _tz.utc
from pathlib import Path
from typing import Union, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.task import (
    AudioAnalysisInfo,
    CoachingAdviceItem,
    ConflictDetail,
    CosVideoItem,
    CosVideoListResponse,
    DeviationItem,
    ExpertVideoRequest,
    ExtractedTechPoint,
    MotionAnalysisItem,
    ResultSummary,
    TaskDeleteResponse,
    TaskResultAthleteResponse,
    TaskResultExpertResponse,
    TaskStatusResponse,
    TaskSubmitResponse,
)
from src.api.schemas.teaching_tip import TeachingTipRef
from src.config import get_settings
from src.db.session import get_db
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
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
from src.workers.expert_video_task import process_expert_video

router = APIRouter(tags=["tasks"])


# ── GET /tasks/cos-videos ────────────────────────────────────────────────────

@router.get("/tasks/cos-videos", response_model=CosVideoListResponse)
def list_cos_videos(
    action_type: str = "all",
) -> CosVideoListResponse:
    """List available COS videos filtered by action type.

    Query params:
        action_type: "forehand" | "backhand" | "all" (default: "all")

    Returns video list with cos_object_key ready to submit to POST /tasks/expert-video.
    """
    if action_type not in ("forehand", "backhand", "all"):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_ACTION_TYPE",
                "message": "action_type must be one of: forehand, backhand, all",
            },
        )
    videos = cos_client.list_videos(action_type=action_type)
    return CosVideoListResponse(
        action_type_filter=action_type,
        total=len(videos),
        videos=[CosVideoItem(**v) for v in videos],
    )


# ── POST /tasks/expert-video ─────────────────────────────────────────────────

@router.post("/tasks/expert-video", status_code=202, response_model=TaskSubmitResponse)
async def submit_expert_video(
    body: ExpertVideoRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskSubmitResponse:
    """Submit an expert coaching video for knowledge extraction.

    1. Verify the COS object exists (sync check, fast).
    2. Persist a pending AnalysisTask.
    3. Dispatch the Celery worker.
    4. Return 202 with task_id.
    """
    # Step 1 — COS existence pre-check (avoids queuing tasks that will fail immediately)
    if not cos_client.object_exists(body.cos_object_key):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "COS_OBJECT_NOT_FOUND",
                "message": "指定的 COS 对象不存在或无访问权限",
                "details": {"cos_object_key": body.cos_object_key},
            },
        )

    # Step 1.5 — Early duration check if client supplies it (US3)
    settings = get_settings()
    if body.video_duration_seconds is not None:
        if body.video_duration_seconds > settings.max_video_duration_s:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "VIDEO_TOO_LONG",
                    "message": (
                        f"视频时长 {body.video_duration_seconds:.0f}s 超过上限 "
                        f"{settings.max_video_duration_s}s（{settings.max_video_duration_s // 60} 分钟）"
                    ),
                    "details": {
                        "duration_seconds": body.video_duration_seconds,
                        "max_duration_seconds": settings.max_video_duration_s,
                    },
                },
            )

    # Step 2 — Persist AnalysisTask in pending state
    task = AnalysisTask(
        id=uuid.uuid4(),
        task_type=TaskType.expert_video,
        status=TaskStatus.pending,
        # Use the COS key as the logical filename; size unknown until download
        video_filename=body.cos_object_key,
        video_size_bytes=0,
        video_storage_uri=body.cos_object_key,
    )

    # Feature 006: validate and associate coach if provided
    if body.coach_id is not None:
        coach_result = await db.execute(
            select(Coach).where(Coach.id == body.coach_id)
        )
        coach = coach_result.scalar_one_or_none()
        if coach is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "COACH_NOT_FOUND", "message": "教练不存在"},
            )
        if not coach.is_active:
            raise HTTPException(
                status_code=422,
                detail={"code": "COACH_INACTIVE", "message": "无法关联已停用的教练"},
            )
        task.coach_id = body.coach_id

    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Step 3 — Dispatch Celery task (fire-and-forget)
    # Resolve action_type_hint: explicit > DB classification > lazy classify
    action_type_hint = body.action_type_hint
    if action_type_hint is None:
        from src.models.video_classification import VideoClassification
        from src.services.video_classifier import VideoClassifierService

        vc_result = await db.execute(
            select(VideoClassification).where(
                VideoClassification.cos_object_key == body.cos_object_key
            )
        )
        vc = vc_result.scalar_one_or_none()
        if vc is not None:
            # Use the DB classification (single source of truth — FR-010)
            action_type_hint = vc.action_type
        else:
            # Lazy classify and persist for future calls
            classifier = VideoClassifierService()
            classification = classifier.classify(body.cos_object_key)
            action_type_hint = classification.action_type
            new_vc = VideoClassification(
                cos_object_key=body.cos_object_key,
                coach_name=classification.coach_name,
                tech_category=classification.tech_category,
                tech_sub_category=classification.tech_sub_category,
                tech_detail=classification.tech_detail,
                video_type=classification.video_type,
                action_type=classification.action_type,
                classification_confidence=classification.classification_confidence,
                manually_overridden=False,
            )
            db.add(new_vc)
            await db.commit()

    process_expert_video.delay(
        str(task.id),
        body.cos_object_key,
        body.enable_audio_analysis,
        body.audio_language,
        action_type_hint,
    )

    # Step 4 — Return 202
    return TaskSubmitResponse(
        task_id=task.id,
        status=task.status.value,
        cos_object_key=body.cos_object_key,
        estimated_completion_seconds=300,
    )


# ── POST /tasks/athlete-video ────────────────────────────────────────────────

@router.post("/tasks/athlete-video", status_code=202, response_model=TaskSubmitResponse)
async def submit_athlete_video(
    video: UploadFile = File(..., description="运动员视频文件"),
    knowledge_base_version: Optional[str] = Form(None, description="指定知识库版本（可选，默认使用 active 版本）"),
    target_person_index: Optional[int] = Form(None, description="多人场景中目标人员索引（可选，默认 0）"),
    db: AsyncSession = Depends(get_db),
) -> TaskSubmitResponse:
    """Submit an athlete video for deviation analysis.

    Accepts multipart/form-data with the video file and optional parameters.
    Returns 202 with task_id immediately; analysis runs asynchronously.
    """
    settings = get_settings()

    # Validate file presence
    if video.filename is None or video.filename == "":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MISSING_VIDEO",
                "message": "请上传视频文件",
                "details": {},
            },
        )

    # Save uploaded file to temp directory
    tmp_dir = Path(settings.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_filename = f"{uuid.uuid4()}_{video.filename}"
    tmp_path = tmp_dir / tmp_filename

    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(video.file, f)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "UPLOAD_FAILED",
                "message": "视频文件保存失败",
                "details": {"error": str(exc)},
            },
        ) from exc
    finally:
        await video.close()

    file_size = tmp_path.stat().st_size

    # Create AnalysisTask
    task = AnalysisTask(
        id=uuid.uuid4(),
        task_type=TaskType.athlete_video,
        status=TaskStatus.pending,
        video_filename=video.filename,
        video_size_bytes=file_size,
        video_storage_uri=str(tmp_path),
        knowledge_base_version=knowledge_base_version,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Dispatch Celery task
    from src.workers.athlete_video_task import process_athlete_video
    process_athlete_video.delay(
        str(task.id),
        str(tmp_path),
        knowledge_base_version,
        target_person_index,
    )

    return TaskSubmitResponse(
        task_id=task.id,
        status=task.status.value,
        knowledge_base_version=knowledge_base_version,
        estimated_completion_seconds=300,
    )


# ── GET /tasks/{task_id} ─────────────────────────────────────────────────────

@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> TaskStatusResponse:
    """Return current status and metadata for a task.

    Returns 404 if the task does not exist or has been soft-deleted.
    """
    # Validate UUID format before hitting the DB
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TASK_NOT_FOUND",
                "message": "任务不存在",
                "details": {"task_id": task_id},
            },
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
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TASK_NOT_FOUND",
                "message": "任务不存在",
                "details": {"task_id": task_id},
            },
        )

    return TaskStatusResponse(
        task_id=task.id,
        task_type=task.task_type.value,
        status=task.status.value,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        video_duration_seconds=task.video_duration_seconds,
        video_fps=task.video_fps,
        video_resolution=task.video_resolution,
        progress_pct=task.progress_pct,
        processed_segments=task.processed_segments,
        total_segments=task.total_segments,
        audio_fallback_reason=task.audio_fallback_reason,
        knowledge_base_version=task.knowledge_base_version,
        # Feature 006: coach info via relationship
        coach_id=task.coach_id,
        coach_name=task.coach.name if task.coach else None,
    )


# ── GET /tasks/{task_id}/result ──────────────────────────────────────────────

@router.get("/tasks/{task_id}/result")
async def get_task_result(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> Union[TaskResultExpertResponse, TaskResultAthleteResponse]:
    """Return the full analysis result for a completed task.

    - expert_video: returns KB draft version + extracted tech points list.
    - athlete_video: returns motion analyses with deviation reports and coaching advice.

    Returns 404 if the task does not exist or has been soft-deleted.
    Returns 409 if the task has not yet reached status=success.
    """
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TASK_NOT_FOUND",
                "message": "任务不存在",
                "details": {"task_id": task_id},
            },
        )

    result = await db.execute(
        select(AnalysisTask).where(
            AnalysisTask.id == task_uuid,
            AnalysisTask.deleted_at.is_(None),
        )
    )
    task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TASK_NOT_FOUND",
                "message": "任务不存在",
                "details": {"task_id": task_id},
            },
        )

    if task.status != TaskStatus.success:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TASK_NOT_READY",
                "message": f"任务尚未完成，当前状态: {task.status.value}",
                "details": {"task_id": task_id, "status": task.status.value},
            },
        )

    # ── expert_video branch ───────────────────────────────────────────────────
    if task.task_type == TaskType.expert_video:
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

        return TaskResultExpertResponse(
            task_id=task.id,
            knowledge_base_version_draft=task.knowledge_base_version,
            extracted_points_count=len(extracted),
            extracted_points=extracted,
            pending_approval=pending_approval,
            audio_analysis=audio_info,
            conflicts=conflicts,
        )

    # ── athlete_video branch ──────────────────────────────────────────────────
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

    return TaskResultAthleteResponse(
        task_id=task.id,
        knowledge_base_version=task.knowledge_base_version or "",
        motion_analyses=motion_analysis_items,
        summary=summary,
    )


# ── DELETE /tasks/{task_id} ──────────────────────────────────────────────────

@router.delete("/tasks/{task_id}", response_model=TaskDeleteResponse)
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> TaskDeleteResponse:
    """Soft-delete a task and all its associated data.

    Sets deleted_at to now; physical cleanup runs on a daily schedule.
    Returns 404 if the task does not exist or is already deleted.
    """
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TASK_NOT_FOUND",
                "message": "任务不存在",
                "details": {"task_id": task_id},
            },
        )

    result = await db.execute(
        select(AnalysisTask).where(
            AnalysisTask.id == task_uuid,
            AnalysisTask.deleted_at.is_(None),
        )
    )
    task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TASK_NOT_FOUND",
                "message": "任务不存在",
                "details": {"task_id": task_id},
            },
        )

    now = datetime.now(UTC)
    task.deleted_at = now
    await db.commit()

    return TaskDeleteResponse(
        task_id=task.id,
        deleted_at=now,
        message="任务及关联数据已标记删除，将在 24 小时内物理清除",
    )
