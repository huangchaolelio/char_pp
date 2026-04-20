"""Teaching Tips router — CRUD management and re-trigger extraction (Feature 005).

Endpoints:
  GET  /teaching-tips                        → list with filters
  PATCH /teaching-tips/{id}                  → human edit
  DELETE /teaching-tips/{id}                 → physical delete
  POST /tasks/{task_id}/extract-tips         → re-trigger extraction (202)
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.teaching_tip import (
    ExtractTipsResponse,
    TeachingTipListResponse,
    TeachingTipPatch,
    TeachingTipResponse,
)
from src.db.session import get_db
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.audio_transcript import AudioTranscript
from src.models.teaching_tip import TeachingTip

logger = logging.getLogger(__name__)
router = APIRouter(tags=["teaching-tips"])


# ── GET /teaching-tips ────────────────────────────────────────────────────────

@router.get("/teaching-tips", response_model=TeachingTipListResponse)
async def list_teaching_tips(
    action_type: Optional[str] = None,
    tech_phase: Optional[str] = None,
    source_type: Optional[str] = None,
    task_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
) -> TeachingTipListResponse:
    """List teaching tips with optional filters."""
    stmt = select(TeachingTip)

    if action_type is not None:
        stmt = stmt.where(TeachingTip.action_type == action_type)
    if tech_phase is not None:
        stmt = stmt.where(TeachingTip.tech_phase == tech_phase)
    if source_type is not None:
        stmt = stmt.where(TeachingTip.source_type == source_type)
    if task_id is not None:
        stmt = stmt.where(TeachingTip.task_id == task_id)

    stmt = stmt.order_by(TeachingTip.source_type.desc(), TeachingTip.confidence.desc())
    result = await db.execute(stmt)
    tips = result.scalars().all()

    return TeachingTipListResponse(
        total=len(tips),
        items=[TeachingTipResponse.model_validate(t) for t in tips],
    )


# ── PATCH /teaching-tips/{id} ─────────────────────────────────────────────────

@router.patch("/teaching-tips/{tip_id}", response_model=TeachingTipResponse)
async def update_teaching_tip(
    tip_id: uuid.UUID,
    body: TeachingTipPatch,
    db: AsyncSession = Depends(get_db),
) -> TeachingTipResponse:
    """Human-edit a teaching tip. Sets source_type='human', preserves original AI text."""
    result = await db.execute(
        select(TeachingTip).where(TeachingTip.id == tip_id)
    )
    tip = result.scalar_one_or_none()

    if tip is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TIP_NOT_FOUND",
                "message": "教学建议条目不存在",
                "details": {"id": str(tip_id)},
            },
        )

    if body.tip_text is not None and body.tip_text != tip.tip_text:
        # Preserve original AI text before overwriting
        if tip.source_type == "auto":
            tip.original_text = tip.tip_text
        tip.tip_text = body.tip_text
        tip.source_type = "human"

    if body.tech_phase is not None:
        tip.tech_phase = body.tech_phase
        tip.source_type = "human"

    await db.commit()
    await db.refresh(tip)
    return TeachingTipResponse.model_validate(tip)


# ── DELETE /teaching-tips/{id} ────────────────────────────────────────────────

@router.delete("/teaching-tips/{tip_id}", status_code=204)
async def delete_teaching_tip(
    tip_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Physically delete a teaching tip."""
    result = await db.execute(
        select(TeachingTip).where(TeachingTip.id == tip_id)
    )
    tip = result.scalar_one_or_none()

    if tip is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TIP_NOT_FOUND",
                "message": "教学建议条目不存在",
                "details": {"id": str(tip_id)},
            },
        )

    await db.delete(tip)
    await db.commit()


# ── POST /tasks/{task_id}/extract-tips ───────────────────────────────────────

@router.post(
    "/tasks/{task_id}/extract-tips",
    status_code=202,
    response_model=ExtractTipsResponse,
)
async def extract_tips(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ExtractTipsResponse:
    """Re-trigger (or first-time trigger) teaching tip extraction for a completed expert video task.

    - Validates task exists, is expert_video type, has status=success, and has an AudioTranscript.
    - Deletes old auto-status tips for this task.
    - Preserves human-status tips.
    - Dispatches extraction synchronously in a background thread (keeps response <1s).

    Returns 202 immediately; extraction completes within ~30s.
    """
    # Validate task
    task_result = await db.execute(
        select(AnalysisTask).where(
            AnalysisTask.id == task_id,
            AnalysisTask.deleted_at.is_(None),
        )
    )
    task = task_result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"},
        )

    if task.task_type != TaskType.expert_video:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "WRONG_TASK_TYPE",
                "message": "仅支持 expert_video 类型任务",
                "details": {"task_type": task.task_type.value},
            },
        )

    if task.status not in (TaskStatus.success, TaskStatus.partial_success):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TASK_NOT_READY",
                "message": f"任务尚未完成，当前状态: {task.status.value}",
            },
        )

    # Check AudioTranscript exists
    at_result = await db.execute(
        select(AudioTranscript).where(AudioTranscript.task_id == task_id)
    )
    transcript = at_result.scalar_one_or_none()

    if transcript is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NO_AUDIO_TRANSCRIPT",
                "message": "该任务无音频转录记录，无法提炼教学建议",
            },
        )

    # Count preserved human tips (for response metadata)
    human_result = await db.execute(
        select(TeachingTip).where(
            TeachingTip.task_id == task_id,
            TeachingTip.source_type == "human",
        )
    )
    human_tips = human_result.scalars().all()
    preserved_human_count = len(human_tips)

    # Delete old auto tips
    await db.execute(
        delete(TeachingTip).where(
            TeachingTip.task_id == task_id,
            TeachingTip.source_type == "auto",
        )
    )
    await db.commit()

    # Resolve action_type from video_classification or task hint
    action_type = _resolve_action_type(task)

    # Run extraction in a background thread to avoid blocking the event loop
    import asyncio
    asyncio.create_task(
        _run_extraction_async(task_id, transcript.sentences or [], action_type)
    )

    logger.info(
        "extract_tips triggered task_id=%s action_type=%s preserved_human=%d",
        task_id, action_type, preserved_human_count,
    )

    return ExtractTipsResponse(
        task_id=task_id,
        status="extracting",
        message="教学建议提炼已触发，将在30秒内完成",
        preserved_human_count=preserved_human_count,
    )


def _resolve_action_type(task: AnalysisTask) -> str:
    """Resolve action_type from task video_filename heuristic (fallback: forehand_topspin)."""
    filename = task.video_filename or ""
    if "反手" in filename:
        return "backhand_push"
    return "forehand_topspin"


async def _run_extraction_async(
    task_id: uuid.UUID,
    sentences: list[dict],
    action_type: str,
) -> None:
    """Background extraction: call LLM and persist results."""
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from src.config import get_settings
        from src.models.teaching_tip import TeachingTip
        from src.services.teaching_tip_extractor import TeachingTipExtractor

        settings = get_settings()
        extractor = TeachingTipExtractor(
            openai_api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_s=settings.openai_timeout_s,
        )

        tips_data = extractor.extract(
            sentences=sentences,
            action_type=action_type,
            task_id=task_id,
        )

        if not tips_data:
            logger.info("extract_tips: no tips extracted for task_id=%s", task_id)
            return

        # Persist new tips
        engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=2)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            async with session.begin():
                for tip in tips_data:
                    session.add(TeachingTip(
                        task_id=tip.task_id,
                        action_type=tip.action_type,
                        tech_phase=tip.tech_phase,
                        tip_text=tip.tip_text,
                        confidence=tip.confidence,
                        source_type="auto",
                    ))

        logger.info(
            "extract_tips: saved %d tips for task_id=%s", len(tips_data), task_id
        )

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "extract_tips background task failed task_id=%s: %s", task_id, exc
        )
