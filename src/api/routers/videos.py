"""Videos router — classification endpoints (Feature-004).

Endpoints:
    GET  /videos/classifications           — list all classifications (filterable)
    POST /videos/classifications/refresh   — trigger full rescan (skip overridden)
    PATCH /videos/classifications/{key}    — human override
    POST /videos/classifications/batch-submit — dispatch Celery tasks by filter
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as _tz
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.video_classification import (
    BatchSubmitRequest,
    BatchSubmitResponse,
    RefreshResponse,
    VideoClassificationListResponse,
    VideoClassificationPatch,
    VideoClassificationResponse,
)
from src.db.session import get_db
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.video_classification import VideoClassification
from src.services import cos_client
from src.services.video_classifier import VideoClassifierService


def _list_all_cos_videos() -> list[dict]:
    """List all mp4 files under COS_VIDEO_ALL_COCAH prefix (all coaches)."""
    from qcloud_cos import CosConfig, CosS3Client  # type: ignore[import]
    from src.config import get_settings

    settings = get_settings()
    config = CosConfig(
        Region=settings.cos_region,
        SecretId=settings.cos_secret_id,
        SecretKey=settings.cos_secret_key,
        Scheme="https",
    )
    client = CosS3Client(config)
    bucket = settings.cos_bucket
    prefix = settings.cos_video_all_cocah

    results = []
    marker = ""
    while True:
        kwargs = dict(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
        if marker:
            kwargs["Marker"] = marker
        response = client.list_objects(**kwargs)
        for obj in response.get("Contents", []):
            if int(obj["Size"]) == 0:
                continue
            key: str = obj["Key"]
            if key.lower().endswith(".mp4"):
                results.append({"cos_object_key": key, "filename": key.split("/")[-1]})
        if response.get("IsTruncated") == "true":
            marker = response["NextMarker"]
        else:
            break
    return results

UTC = _tz.utc

router = APIRouter(prefix="/videos", tags=["videos"])

# Module-level singleton — loaded once on first import
_classifier: Optional[VideoClassifierService] = None


def _get_classifier() -> VideoClassifierService:
    global _classifier
    if _classifier is None:
        _classifier = VideoClassifierService()
    return _classifier


def _to_response(vc: VideoClassification) -> VideoClassificationResponse:
    return VideoClassificationResponse(
        cos_object_key=vc.cos_object_key,
        coach_name=vc.coach_name,
        tech_category=vc.tech_category,
        tech_sub_category=vc.tech_sub_category,
        tech_detail=vc.tech_detail,
        video_type=vc.video_type,
        action_type=vc.action_type,
        classification_confidence=vc.classification_confidence,
        manually_overridden=vc.manually_overridden,
        override_reason=vc.override_reason,
        classified_at=vc.classified_at,
        updated_at=vc.updated_at,
    )


# ── GET /videos/classifications ───────────────────────────────────────────────

@router.get("/classifications", response_model=VideoClassificationListResponse)
async def list_classifications(
    coach_name: Optional[str] = None,
    tech_category: Optional[str] = None,
    tech_detail: Optional[str] = None,
    action_type: Optional[str] = None,
    video_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> VideoClassificationListResponse:
    """Return all video classifications, optionally filtered."""
    stmt = select(VideoClassification)
    if coach_name is not None:
        stmt = stmt.where(VideoClassification.coach_name == coach_name)
    if tech_category is not None:
        stmt = stmt.where(VideoClassification.tech_category == tech_category)
    if tech_detail is not None:
        stmt = stmt.where(VideoClassification.tech_detail == tech_detail)
    if action_type is not None:
        stmt = stmt.where(VideoClassification.action_type == action_type)
    if video_type is not None:
        stmt = stmt.where(VideoClassification.video_type == video_type)
    stmt = stmt.order_by(VideoClassification.cos_object_key)

    result = await db.execute(stmt)
    records = result.scalars().all()
    return VideoClassificationListResponse(
        total=len(records),
        items=[_to_response(r) for r in records],
    )


# ── POST /videos/classifications/refresh ─────────────────────────────────────

@router.post("/classifications/refresh", response_model=RefreshResponse)
async def refresh_classifications(
    db: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    """Scan all COS videos and (re-)classify them.

    Records with ``manually_overridden=True`` are never touched.
    """
    classifier = _get_classifier()

    # Fetch all videos from COS (all coaches, using COS_VIDEO_ALL_COCAH prefix)
    all_videos = _list_all_cos_videos()
    total_scanned = len(all_videos)

    # Load existing overridden keys so we can skip them
    overridden_result = await db.execute(
        select(VideoClassification.cos_object_key).where(
            VideoClassification.manually_overridden.is_(True)
        )
    )
    overridden_keys: set[str] = {row[0] for row in overridden_result.fetchall()}

    refreshed = 0
    skipped = 0
    now = datetime.now(UTC)

    for video in all_videos:
        key = video["cos_object_key"]

        if key in overridden_keys:
            skipped += 1
            continue

        result = classifier.classify(key)

        # Upsert: fetch existing record or create new one
        existing_result = await db.execute(
            select(VideoClassification).where(VideoClassification.cos_object_key == key)
        )
        existing = existing_result.scalar_one_or_none()

        if existing is None:
            vc = VideoClassification(
                cos_object_key=key,
                coach_name=result.coach_name,
                tech_category=result.tech_category,
                tech_sub_category=result.tech_sub_category,
                tech_detail=result.tech_detail,
                video_type=result.video_type,
                action_type=result.action_type,
                classification_confidence=result.classification_confidence,
                manually_overridden=False,
            )
            db.add(vc)
        else:
            existing.coach_name = result.coach_name
            existing.tech_category = result.tech_category
            existing.tech_sub_category = result.tech_sub_category
            existing.tech_detail = result.tech_detail
            existing.video_type = result.video_type
            existing.action_type = result.action_type
            existing.classification_confidence = result.classification_confidence
            existing.updated_at = now

        refreshed += 1

    await db.commit()
    return RefreshResponse(
        refreshed=refreshed,
        skipped=skipped,
        total_scanned=total_scanned,
    )


# ── PATCH /videos/classifications/{cos_object_key} ───────────────────────────

@router.patch("/classifications/{cos_object_key:path}", response_model=VideoClassificationResponse)
async def override_classification(
    cos_object_key: str,
    body: VideoClassificationPatch,
    db: AsyncSession = Depends(get_db),
) -> VideoClassificationResponse:
    """Manually override a video classification. Sets ``manually_overridden=True``
    so future automated refreshes will not overwrite this record.
    """
    # Decode percent-encoded path (e.g. %2F → /)
    cos_object_key = unquote(cos_object_key)

    result = await db.execute(
        select(VideoClassification).where(
            VideoClassification.cos_object_key == cos_object_key
        )
    )
    vc = result.scalar_one_or_none()

    if vc is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CLASSIFICATION_NOT_FOUND",
                "message": "未找到该视频的分类记录",
                "details": {"cos_object_key": cos_object_key},
            },
        )

    # Apply the patch fields that were provided
    if body.tech_category is not None:
        vc.tech_category = body.tech_category
    if body.tech_sub_category is not None:
        vc.tech_sub_category = body.tech_sub_category
    if body.tech_detail is not None:
        vc.tech_detail = body.tech_detail
    if body.action_type is not None:
        vc.action_type = body.action_type
    if body.video_type is not None:
        vc.video_type = body.video_type
    if body.override_reason is not None:
        vc.override_reason = body.override_reason

    vc.manually_overridden = True
    vc.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(vc)
    return _to_response(vc)


# ── POST /videos/classifications/batch-submit ─────────────────────────────────

@router.post("/classifications/batch-submit", response_model=BatchSubmitResponse)
async def batch_submit_classifications(
    body: BatchSubmitRequest,
    db: AsyncSession = Depends(get_db),
) -> BatchSubmitResponse:
    """Query video_classifications by filter and dispatch expert-video Celery tasks.

    Only submits videos that pass the filter. Returns list of created task IDs.
    """
    from src.workers.expert_video_task import process_expert_video

    # Build filter query
    stmt = select(VideoClassification)
    if body.coach_name is not None:
        stmt = stmt.where(VideoClassification.coach_name == body.coach_name)
    if body.tech_category is not None:
        stmt = stmt.where(VideoClassification.tech_category == body.tech_category)
    if body.tech_detail is not None:
        stmt = stmt.where(VideoClassification.tech_detail == body.tech_detail)
    if body.action_type is not None:
        stmt = stmt.where(VideoClassification.action_type == body.action_type)
    if body.video_type is not None:
        stmt = stmt.where(VideoClassification.video_type == body.video_type)

    result = await db.execute(stmt)
    records = result.scalars().all()

    task_ids: list[str] = []
    for vc in records:
        # Skip videos that don't exist in COS (defensive check)
        if not cos_client.object_exists(vc.cos_object_key):
            continue

        task = AnalysisTask(
            id=uuid.uuid4(),
            task_type=TaskType.expert_video,
            status=TaskStatus.pending,
            video_filename=vc.cos_object_key,
            video_size_bytes=0,
            video_storage_uri=vc.cos_object_key,
        )
        db.add(task)
        await db.flush()  # get the ID before dispatch

        process_expert_video.delay(
            str(task.id),
            vc.cos_object_key,
            body.enable_audio_analysis,
            body.audio_language,
            vc.action_type,
        )
        task_ids.append(str(task.id))

    await db.commit()
    return BatchSubmitResponse(submitted=len(task_ids), task_ids=task_ids)
