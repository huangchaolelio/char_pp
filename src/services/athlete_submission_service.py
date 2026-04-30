"""Feature-020 · AthleteSubmissionService.

集中管控运动员侧预处理 / 诊断任务提交的**预校验 + 调度**，屏蔽 router 层业务判断：

预处理 (US2):
  - 校验 `athlete_video_classification_id` 存在 → 否则 ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND
  - 透传 `cos_object_key` 给 `preprocessing_service.create_or_reuse`（复用 F-016 底座）
  - 提交成功后 chain 一个 `mark_athlete_preprocessed_cb` 回写 athlete_video_classifications

诊断 (US3):
  - 校验素材存在 + `preprocessed=true` → 否则 ATHLETE_VIDEO_NOT_PREPROCESSED
  - 校验 `tech_category` 的 active standard 存在 → 否则 STANDARD_NOT_AVAILABLE
  - 创建 `analysis_tasks(task_type=athlete_diagnosis)` + 入队 `diagnose_athlete`，
    payload 传 `classification_id` 走新分支（worker 层分流）
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.athlete_video_classification import AthleteVideoClassification
from src.models.tech_standard import StandardStatus, TechStandard

logger = logging.getLogger(__name__)


# ── DTOs ─────────────────────────────────────────────────────────────────


@dataclass
class AthletePreprocessingOutcome:
    job_id: UUID
    athlete_video_classification_id: UUID
    cos_object_key: str
    status: str
    reused: bool
    segment_count: int | None
    has_audio: bool
    started_at: Any
    completed_at: Any
    task_id: UUID | None = None


@dataclass
class AthleteBatchSubmittedItem:
    athlete_video_classification_id: UUID
    job_id: UUID | None = None
    task_id: UUID | None = None
    reused: bool = False


@dataclass
class AthleteBatchRejectedItem:
    athlete_video_classification_id: UUID
    error_code: str
    message: str


@dataclass
class AthleteBatchOutcome:
    submitted: list[AthleteBatchSubmittedItem]
    rejected: list[AthleteBatchRejectedItem]


@dataclass
class AthleteDiagnosisOutcome:
    task_id: UUID
    athlete_video_classification_id: UUID
    tech_category: str
    status: str


# ── 内部 helpers ─────────────────────────────────────────────────────────


async def _fetch_classification_row(
    db: AsyncSession, classification_id: UUID
) -> AthleteVideoClassification | None:
    stmt = select(AthleteVideoClassification).where(
        AthleteVideoClassification.id == classification_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _has_active_standard(db: AsyncSession, tech_category: str) -> bool:
    stmt = select(TechStandard).where(
        TechStandard.tech_category == tech_category,
        TechStandard.status == StandardStatus.active,
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


# ══════════════════════════════════════════════════════════════════════════
# US2 · PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════


async def submit_athlete_preprocessing(
    db: AsyncSession,
    *,
    classification_id: UUID,
    force: bool = False,
) -> AthletePreprocessingOutcome:
    """Submit a single athlete preprocessing job.

    Raises:
        AppException(ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND): id 不存在
        AppException(CHANNEL_QUEUE_FULL): preprocessing 通道满
    """
    from src.services import preprocessing_service as _ps

    row = await _fetch_classification_row(db, classification_id)
    if row is None:
        raise AppException(
            ErrorCode.ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND,
            details={"resource_id": str(classification_id)},
        )

    try:
        outcome = await _ps.create_or_reuse(
            db, cos_object_key=row.cos_object_key, force=force,
        )
    except _ps.ChannelQueueFullError as exc:
        raise AppException(
            ErrorCode.CHANNEL_QUEUE_FULL,
            message=str(exc),
            details={"channel": "preprocessing"},
        ) from exc
    except _ps.CosKeyNotClassifiedError as exc:
        # 双边回退仍查不到 → 视作运动员素材记录不存在
        raise AppException(
            ErrorCode.ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND,
            details={"resource_id": str(classification_id)},
        ) from exc

    await db.commit()

    if not outcome.reused:
        # 触发 Celery chain: preprocess_video → mark_athlete_preprocessed_cb
        _dispatch_preprocessing_chain(
            job_id=outcome.job_id,
            cos_object_key=row.cos_object_key,
        )
    else:
        # 命中 reused 分支：直接回写 preprocessed=True（幂等）
        async with db.begin() if not db.in_transaction() else _noop_ctx():
            await _ps.mark_athlete_preprocessed(
                db,
                cos_object_key=row.cos_object_key,
                preprocessing_job_id=outcome.job_id,
            )
        await db.commit()

    return AthletePreprocessingOutcome(
        job_id=outcome.job_id,
        athlete_video_classification_id=classification_id,
        cos_object_key=outcome.cos_object_key,
        status=outcome.status,
        reused=outcome.reused,
        segment_count=outcome.segment_count,
        has_audio=bool(outcome.has_audio),
        started_at=outcome.started_at,
        completed_at=outcome.completed_at,
    )


class _noop_ctx:
    """Dummy async context manager for when session already in txn."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _dispatch_preprocessing_chain(*, job_id: UUID, cos_object_key: str) -> None:
    """Enqueue preprocess_video → mark_athlete_preprocessed_cb (chained)."""
    from celery import chain

    from src.workers.athlete_preprocessing_callback import (
        mark_athlete_preprocessed_cb,
    )
    from src.workers.preprocessing_task import preprocess_video

    (
        preprocess_video.si(str(job_id))
        | mark_athlete_preprocessed_cb.si(str(job_id), cos_object_key)
    ).apply_async()


async def submit_athlete_preprocessing_batch(
    db: AsyncSession,
    *,
    items: list[tuple[UUID, bool]],
) -> AthleteBatchOutcome:
    """Batch submit; per-item isolation; preserve order."""
    submitted: list[AthleteBatchSubmittedItem] = []
    rejected: list[AthleteBatchRejectedItem] = []

    for classification_id, force in items:
        try:
            out = await submit_athlete_preprocessing(
                db, classification_id=classification_id, force=force,
            )
            submitted.append(AthleteBatchSubmittedItem(
                athlete_video_classification_id=classification_id,
                job_id=out.job_id,
                reused=out.reused,
            ))
        except AppException as exc:
            # 通道满这种整批级别错误 → 抛出让 router 整批 503
            if exc.code == ErrorCode.CHANNEL_QUEUE_FULL:
                raise
            rejected.append(AthleteBatchRejectedItem(
                athlete_video_classification_id=classification_id,
                error_code=exc.code.value,
                message=exc.message or "",
            ))

    return AthleteBatchOutcome(submitted=submitted, rejected=rejected)


# ══════════════════════════════════════════════════════════════════════════
# US3 · DIAGNOSIS
# ══════════════════════════════════════════════════════════════════════════


async def submit_athlete_diagnosis(
    db: AsyncSession,
    *,
    classification_id: UUID,
    force: bool = False,
) -> AthleteDiagnosisOutcome:
    """Submit a single athlete diagnosis task.

    Pre-checks: row exists, preprocessed=true, active standard exists.

    Raises:
        AppException(ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND): 404
        AppException(ATHLETE_VIDEO_NOT_PREPROCESSED): 409
        AppException(STANDARD_NOT_AVAILABLE): 409
        AppException(CHANNEL_QUEUE_FULL): 503
    """
    row = await _fetch_classification_row(db, classification_id)
    if row is None:
        raise AppException(
            ErrorCode.ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND,
            details={"resource_id": str(classification_id)},
        )

    if not row.preprocessed:
        raise AppException(
            ErrorCode.ATHLETE_VIDEO_NOT_PREPROCESSED,
            details={
                "athlete_video_classification_id": str(classification_id),
                "cos_object_key": row.cos_object_key,
            },
        )

    tech_category = row.tech_category
    if not await _has_active_standard(db, tech_category):
        raise AppException(
            ErrorCode.STANDARD_NOT_AVAILABLE,
            details={
                "tech_category": tech_category,
                "hint": "请在 KB 管理页发布对应类别的 published 标准",
            },
        )

    # 通道容量门控（复用 F-013 diagnosis 通道）
    from src.services.task_channel_service import TaskChannelService

    channels = TaskChannelService()
    cfg = await channels.load_config(db, TaskType.athlete_diagnosis)
    if not cfg.enabled:
        raise AppException(
            ErrorCode.CHANNEL_DISABLED,
            details={"channel": "diagnosis"},
        )

    # 活跃任务计数
    from sqlalchemy import func

    inflight_stmt = (
        select(func.count()).select_from(AnalysisTask)
        .where(
            AnalysisTask.task_type == TaskType.athlete_diagnosis,
            AnalysisTask.status.in_([TaskStatus.pending, TaskStatus.processing]),
        )
    )
    inflight = int((await db.execute(inflight_stmt)).scalar_one())
    if inflight >= cfg.queue_capacity:
        raise AppException(
            ErrorCode.CHANNEL_QUEUE_FULL,
            details={"channel": "diagnosis", "inflight": inflight},
        )

    # 创建 analysis_tasks 行
    new_id = uuid.uuid4()
    task_row = AnalysisTask(
        id=new_id,
        task_type=TaskType.athlete_diagnosis,
        video_filename=row.cos_object_key.rsplit("/", 1)[-1],
        video_size_bytes=0,
        video_storage_uri=row.cos_object_key,
        cos_object_key=row.cos_object_key,
        status=TaskStatus.pending,
        submitted_via="single",
    )
    db.add(task_row)
    await db.commit()

    # 入队 diagnose_athlete，携带 classification_id 走新分支
    from src.workers.athlete_diagnosis_task import diagnose_athlete

    diagnose_athlete.apply_async(
        kwargs={
            "task_id": str(new_id),
            "video_storage_uri": row.cos_object_key,
            "knowledge_base_version": None,
            "classification_id": str(classification_id),
        },
        queue="diagnosis",
    )

    return AthleteDiagnosisOutcome(
        task_id=new_id,
        athlete_video_classification_id=classification_id,
        tech_category=tech_category,
        status="pending",
    )


async def submit_athlete_diagnosis_batch(
    db: AsyncSession,
    *,
    items: list[tuple[UUID, bool]],
) -> AthleteBatchOutcome:
    """Batch submit; channel-full = atomic whole-batch 503.

    容量预检：在逐条处理前先确认剩余槽位 >= len(items)；不足则整批拒绝。
    """
    from sqlalchemy import func

    from src.services.task_channel_service import TaskChannelService

    channels = TaskChannelService()
    cfg = await channels.load_config(db, TaskType.athlete_diagnosis)
    if not cfg.enabled:
        raise AppException(
            ErrorCode.CHANNEL_DISABLED, details={"channel": "diagnosis"},
        )

    inflight_stmt = (
        select(func.count()).select_from(AnalysisTask)
        .where(
            AnalysisTask.task_type == TaskType.athlete_diagnosis,
            AnalysisTask.status.in_([TaskStatus.pending, TaskStatus.processing]),
        )
    )
    inflight = int((await db.execute(inflight_stmt)).scalar_one())
    remaining = max(0, cfg.queue_capacity - inflight)
    if remaining < len(items):
        raise AppException(
            ErrorCode.CHANNEL_QUEUE_FULL,
            details={
                "channel": "diagnosis",
                "requested": len(items),
                "remaining": remaining,
            },
        )

    submitted: list[AthleteBatchSubmittedItem] = []
    rejected: list[AthleteBatchRejectedItem] = []

    for classification_id, force in items:
        try:
            out = await submit_athlete_diagnosis(
                db, classification_id=classification_id, force=force,
            )
            submitted.append(AthleteBatchSubmittedItem(
                athlete_video_classification_id=classification_id,
                task_id=out.task_id,
                reused=False,
            ))
        except AppException as exc:
            if exc.code == ErrorCode.CHANNEL_QUEUE_FULL:
                raise  # re-raise atomic failure
            rejected.append(AthleteBatchRejectedItem(
                athlete_video_classification_id=classification_id,
                error_code=exc.code.value,
                message=exc.message or "",
            ))

    return AthleteBatchOutcome(submitted=submitted, rejected=rejected)
