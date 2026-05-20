"""Feature-021 内容清洗编排服务.

两类公开入口：

- :func:`submit_curation` / :func:`submit_curation_batch` — 由 router 在
  ``POST /api/v1/tasks/curation`` 中调用，负责前置校验 + 通道容量门控
  + ``analysis_tasks`` + ``video_curation_jobs`` 双行 INSERT + Celery dispatch。
  幂等：默认相同 ``cos_object_key`` + ``rubric_version`` 的最近 success 作业
  直接短路返回；``force=true`` 时新建独立行（spec FR-018）。
- :func:`run_curation_job` — 由 ``src/workers/curation_task.py::curate_video``
  Celery 任务在后台调用：load rubric → 遍历预处理分段 → 决策 → 持久化。

视频级摘要派生（spec FR-004 + FR-009）：
    accepted_duration_ratio = sum(seg.duration where effective='accepted') / total_duration
    low_quality = accepted_duration_ratio < rubric.low_quality_ratio
    short_video = total_duration < rubric.short_video_seconds
    audio_unavailable = transcript_sentences 为空（视频无音频或 Whisper skipped）

派生口径在每次成功 / 覆盖时事务内更新（参见 plan.md § R5）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.models.video_curation_job import VideoCurationJob
from src.models.video_curation_segment_result import VideoCurationSegmentResult
from src.models.video_preprocessing_job import (
    PreprocessingJobStatus,
    VideoPreprocessingJob,
)
from src.models.video_preprocessing_segment import VideoPreprocessingSegment
from src.services.curation import rubric_loader
from src.services.curation.decision_engine import DecisionResult, decide
from src.services.curation.rubric_loader import CurationRubric
from src.services.curation.segment_text_provider import extract_segment_text
from src.utils.time_utils import now_cst

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# 数据类（DTO，router 直接转 Pydantic 响应）
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class CurationSubmissionOutcome:
    """单条提交结果。"""

    job_id: uuid.UUID
    task_id: uuid.UUID | None
    cos_object_key: str
    curation_rubric_version: str
    status: str
    queued: bool
    idempotent_short_circuit: bool


@dataclass
class CurationBatchSubmittedItem:
    coach_video_classification_id: uuid.UUID
    job_id: uuid.UUID | None
    task_id: uuid.UUID | None
    queued: bool
    idempotent_short_circuit: bool


@dataclass
class CurationBatchRejectedItem:
    coach_video_classification_id: uuid.UUID
    error_code: str
    message: str


@dataclass
class CurationBatchOutcome:
    submitted: list[CurationBatchSubmittedItem]
    rejected: list[CurationBatchRejectedItem]


# ─────────────────────────────────────────────────────────────────────────
# 提交入口（router 调用）
# ─────────────────────────────────────────────────────────────────────────


async def _fetch_classification(
    db: AsyncSession, classification_id: uuid.UUID
) -> CoachVideoClassification | None:
    stmt = select(CoachVideoClassification).where(
        CoachVideoClassification.id == classification_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _resolve_preprocessing_job(
    db: AsyncSession, cos_object_key: str
) -> VideoPreprocessingJob | None:
    """取最近一次 ``status='success'`` 的预处理作业（按 ``started_at DESC``）。"""
    stmt = (
        select(VideoPreprocessingJob)
        .where(
            VideoPreprocessingJob.cos_object_key == cos_object_key,
            VideoPreprocessingJob.status == PreprocessingJobStatus.success.value,
        )
        .order_by(VideoPreprocessingJob.started_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _find_existing_success_job(
    db: AsyncSession,
    classification_id: uuid.UUID,
) -> VideoCurationJob | None:
    """获取该素材最近一次 ``status='success'`` 的清洗作业（用于幂等短路 + 版本对比）。"""
    stmt = (
        select(VideoCurationJob)
        .where(
            VideoCurationJob.coach_video_classification_id == classification_id,
            VideoCurationJob.status == "success",
        )
        .order_by(VideoCurationJob.completed_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def submit_curation(
    db: AsyncSession,
    *,
    classification_id: uuid.UUID,
    rubric_version: str | None = None,
    force: bool = False,
) -> CurationSubmissionOutcome:
    """提交单条视频清洗任务（spec FR-001 / FR-018）。

    Raises:
        AppException(NOT_FOUND): classification_id 不存在
        AppException(CLASSIFICATION_REQUIRED): tech_category=unclassified
        AppException(PREPROCESSING_JOB_NOT_FOUND): 预处理未完成（沿用既有错误码）
        AppException(RUBRIC_INVALID|RUBRIC_VERSION_NOT_FOUND): 规范文件错
        AppException(CURATION_RUBRIC_MISMATCH): 既有 success 作业版本与本次不一致 + force=false
        AppException(CHANNEL_QUEUE_FULL|CHANNEL_DISABLED): 通道
    """
    row = await _fetch_classification(db, classification_id)
    if row is None:
        raise AppException(
            ErrorCode.NOT_FOUND,
            message="coach_video_classification not found",
            details={"resource_id": str(classification_id)},
        )

    # 1) 必须已分类（tech_category != unclassified）
    if not row.tech_category or row.tech_category == "unclassified":
        raise AppException(
            ErrorCode.CLASSIFICATION_REQUIRED,
            message="tech_category must be set before curation (got 'unclassified')",
            details={
                "coach_video_classification_id": str(classification_id),
                "current_tech_category": row.tech_category,
            },
        )

    # 2) 预处理已完成
    if not row.preprocessed:
        raise AppException(
            ErrorCode.PREPROCESSING_JOB_NOT_FOUND,
            message="video preprocessing has not completed for this classification",
            details={
                "coach_video_classification_id": str(classification_id),
                "cos_object_key": row.cos_object_key,
            },
        )
    pp_job = await _resolve_preprocessing_job(db, row.cos_object_key)
    if pp_job is None:
        raise AppException(
            ErrorCode.PREPROCESSING_JOB_NOT_FOUND,
            message="no successful preprocessing_job for cos_object_key",
            details={"cos_object_key": row.cos_object_key},
        )

    # 3) rubric 加载 + schema 校验（启动期已查过一次，此处依旧调用以拦截
    #    "运营改 v2 后未重启 worker / API 老缓存还在 v1" 之类的事故；
    #    rubric_loader.load 自带 lru_cache 故重复调用零成本）
    rubric = rubric_loader.load(rubric_version) if rubric_version else rubric_loader.load()
    effective_version = rubric.version

    # 4) 幂等短路 / 版本不一致拦截
    if not force:
        existing = await _find_existing_success_job(db, classification_id)
        if existing is not None:
            if existing.curation_rubric_version == effective_version:
                # 同版本短路
                return CurationSubmissionOutcome(
                    job_id=existing.id,
                    task_id=None,
                    cos_object_key=existing.cos_object_key,
                    curation_rubric_version=existing.curation_rubric_version,
                    status=existing.status,
                    queued=False,
                    idempotent_short_circuit=True,
                )
            raise AppException(
                ErrorCode.CURATION_RUBRIC_MISMATCH,
                details={
                    "existing_job_id": str(existing.id),
                    "existing_rubric_version": existing.curation_rubric_version,
                    "submitted_rubric_version": effective_version,
                    "hint": "use force=true to start a new job under the new version",
                },
            )

    # 5) 通道容量门控（清洗复用 default 队列；通道 task_type='video_curation'）
    from src.services.task_channel_service import TaskChannelService

    channels = TaskChannelService()
    cfg = await channels.load_config(db, TaskType.video_curation)
    if not cfg.enabled:
        raise AppException(
            ErrorCode.CHANNEL_DISABLED,
            details={"channel": "video_curation"},
        )
    inflight = int(
        (
            await db.execute(
                select(func.count())
                .select_from(AnalysisTask)
                .where(
                    AnalysisTask.task_type == TaskType.video_curation,
                    AnalysisTask.status.in_([TaskStatus.pending, TaskStatus.processing]),
                )
            )
        ).scalar_one()
    )
    if inflight >= cfg.queue_capacity:
        raise AppException(
            ErrorCode.CHANNEL_QUEUE_FULL,
            details={
                "channel": "video_curation",
                "inflight": inflight,
                "capacity": cfg.queue_capacity,
            },
        )

    # 6) 创建 video_curation_jobs + analysis_tasks（同事务）
    job_id = uuid.uuid4()
    task_id = uuid.uuid4()

    job_row = VideoCurationJob(
        id=job_id,
        cos_object_key=row.cos_object_key,
        coach_video_classification_id=classification_id,
        preprocessing_job_id=pp_job.id,
        curation_rubric_version=effective_version,
        status="pending",
    )
    db.add(job_row)

    task_row = AnalysisTask(
        id=task_id,
        task_type=TaskType.video_curation,
        video_filename=row.filename or row.cos_object_key.rsplit("/", 1)[-1],
        video_size_bytes=0,
        video_storage_uri=row.cos_object_key,
        cos_object_key=row.cos_object_key,
        status=TaskStatus.pending,
        submitted_via="single",
    )
    db.add(task_row)
    await db.commit()

    # 7) 提交 Celery 任务（commit 后再 dispatch；失败由 orphan recovery 兜底）
    try:
        from src.workers.curation_task import curate_video

        curate_video.apply_async(
            kwargs={"task_id": str(task_id), "job_id": str(job_id)},
            queue="default",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "celery enqueue failed after commit: job_id=%s task_id=%s err=%s",
            job_id, task_id, exc,
        )

    return CurationSubmissionOutcome(
        job_id=job_id,
        task_id=task_id,
        cos_object_key=row.cos_object_key,
        curation_rubric_version=effective_version,
        status="pending",
        queued=True,
        idempotent_short_circuit=False,
    )


async def submit_curation_batch(
    db: AsyncSession,
    *,
    items: Sequence[uuid.UUID],
    rubric_version: str | None = None,
    force: bool = False,
) -> CurationBatchOutcome:
    """批量提交；逐条独立处理（与 athlete_submission_service 同惯例）.

    单条失败不回滚整批；逐条结果在 ``submitted[]`` / ``rejected[]`` 中报告。
    """
    submitted: list[CurationBatchSubmittedItem] = []
    rejected: list[CurationBatchRejectedItem] = []

    for cid in items:
        try:
            out = await submit_curation(
                db,
                classification_id=cid,
                rubric_version=rubric_version,
                force=force,
            )
            submitted.append(
                CurationBatchSubmittedItem(
                    coach_video_classification_id=cid,
                    job_id=out.job_id,
                    task_id=out.task_id,
                    queued=out.queued,
                    idempotent_short_circuit=out.idempotent_short_circuit,
                )
            )
        except AppException as exc:
            rejected.append(
                CurationBatchRejectedItem(
                    coach_video_classification_id=cid,
                    error_code=exc.code.value,
                    message=exc.message,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("submit_curation unexpected error for %s", cid)
            rejected.append(
                CurationBatchRejectedItem(
                    coach_video_classification_id=cid,
                    error_code=ErrorCode.INTERNAL_ERROR.value,
                    message=str(exc)[:500],
                )
            )

    return CurationBatchOutcome(submitted=submitted, rejected=rejected)


# ─────────────────────────────────────────────────────────────────────────
# Worker 编排入口（curate_video Celery 任务调用）
# ─────────────────────────────────────────────────────────────────────────


async def _load_segments_for_job(
    db: AsyncSession, preprocessing_job_id: uuid.UUID
) -> list[VideoPreprocessingSegment]:
    stmt = (
        select(VideoPreprocessingSegment)
        .where(VideoPreprocessingSegment.job_id == preprocessing_job_id)
        .order_by(VideoPreprocessingSegment.segment_index.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def _load_transcript_sentences_for_cos(
    db: AsyncSession, cos_object_key: str
) -> list[dict]:
    """从 audio_transcripts 表中取与该视频关联的最近一条 transcript.sentences.

    若无关联 transcript（视频无音频 / 未跑过 Whisper），返回空列表 —— 上层
    将 audio_unavailable 标记为 True，规则路按 rubric 配置降级。

    实现：通过 analysis_tasks.cos_object_key 反查 task → audio_transcripts。
    """
    from src.models.audio_transcript import AudioTranscript

    stmt = (
        select(AudioTranscript.sentences)
        .join(AnalysisTask, AnalysisTask.id == AudioTranscript.task_id)
        .where(AnalysisTask.cos_object_key == cos_object_key)
        .order_by(AudioTranscript.created_at.desc())
        .limit(1)
    )
    sentences = (await db.execute(stmt)).scalar_one_or_none()
    if sentences is None:
        return []
    if not isinstance(sentences, list):
        return []
    return [s for s in sentences if isinstance(s, dict)]


def _aggregate_summary(
    segments: list[VideoPreprocessingSegment],
    decisions: list[DecisionResult],
    rubric: CurationRubric,
    audio_unavailable: bool,
) -> dict[str, Any]:
    """根据逐分段结果派生视频级摘要（spec FR-004 + FR-009）。"""
    accepted_count = sum(1 for d in decisions if d.decision == "accepted")
    rejected_count = sum(1 for d in decisions if d.decision == "rejected")
    uncertain_count = sum(1 for d in decisions if d.decision == "uncertain")

    accepted_duration_ms = 0
    total_duration_ms = 0
    for seg, dec in zip(segments, decisions):
        seg_dur_ms = max(0, seg.end_ms - seg.start_ms)
        total_duration_ms += seg_dur_ms
        if dec.decision == "accepted":
            accepted_duration_ms += seg_dur_ms

    total_duration_seconds = total_duration_ms / 1000.0
    accepted_duration_seconds = accepted_duration_ms / 1000.0
    if total_duration_seconds > 0:
        ratio = accepted_duration_seconds / total_duration_seconds
    else:
        ratio = 0.0

    low_quality = ratio < rubric.low_quality_ratio
    short_video = total_duration_seconds < float(rubric.short_video_seconds)

    return {
        "total_segment_count": len(segments),
        "accepted_segment_count": accepted_count,
        "rejected_segment_count": rejected_count,
        "uncertain_segment_count": uncertain_count,
        "total_duration_seconds": round(total_duration_seconds, 3),
        "accepted_duration_seconds": round(accepted_duration_seconds, 3),
        "accepted_duration_ratio": round(ratio, 4),
        "low_quality": low_quality,
        "audio_unavailable": audio_unavailable,
        "short_video": short_video,
    }


async def _persist_results(
    db: AsyncSession,
    *,
    job: VideoCurationJob,
    segments: list[VideoPreprocessingSegment],
    decisions: list[DecisionResult],
    summary: dict[str, Any],
) -> None:
    """事务内持久化逐分段决策 + 视频级摘要 + 反向同步 coach_video_classifications。"""
    # 1) 逐分段写入
    for seg, dec in zip(segments, decisions):
        db.add(
            VideoCurationSegmentResult(
                job_id=job.id,
                segment_index=seg.segment_index,
                segment_start_ms=seg.start_ms,
                segment_end_ms=seg.end_ms,
                auto_decision=dec.decision,
                validity_score=dec.validity_score,
                rejection_reason=dec.rejection_reason,
                decision_source=dec.decision_source,
                dim_breakdown=dec.dim_breakdown,
            )
        )

    # 2) 更新 job 摘要 + status
    await db.execute(
        update(VideoCurationJob)
        .where(VideoCurationJob.id == job.id)
        .values(
            status="success",
            completed_at=now_cst(),
            **summary,
        )
    )

    # 3) 反向同步 coach_video_classifications（last_curation_job_id + low_quality）
    await db.execute(
        update(CoachVideoClassification)
        .where(CoachVideoClassification.id == job.coach_video_classification_id)
        .values(
            last_curation_job_id=job.id,
            low_quality=summary["low_quality"],
        )
    )

    await db.commit()


async def run_curation_job(db: AsyncSession, job_id: uuid.UUID) -> str:
    """执行清洗作业的实际工作（由 Celery 任务调用）.

    Returns:
        ``"success"`` 或 ``"failed"`` —— 反映 ``video_curation_jobs.status``
        最终值（同步给 analysis_tasks.status 由 Celery 任务主体处理）。

    流程：
        1. 加载 ``video_curation_jobs`` 行 + 标 ``status='running'``
        2. 加载 rubric（按版本号）
        3. 取预处理分段 + transcript（可选）
        4. 逐分段 ``decide`` 收集 ``DecisionResult``
        5. 派生视频级摘要 → 事务内持久化所有结果
        6. 失败时落 ``status='failed'`` + ``error_code/error_message``
    """
    job_row = (
        await db.execute(
            select(VideoCurationJob).where(VideoCurationJob.id == job_id)
        )
    ).scalar_one_or_none()
    if job_row is None:
        raise RuntimeError(f"video_curation_job {job_id} not found")

    # 1) 标 running
    await db.execute(
        update(VideoCurationJob)
        .where(VideoCurationJob.id == job_id)
        .values(status="running", started_at=now_cst())
    )
    await db.commit()

    try:
        # 2) 加载 rubric
        rubric = rubric_loader.load(job_row.curation_rubric_version)

        # 3) 取分段 + transcript
        segments = await _load_segments_for_job(db, job_row.preprocessing_job_id)
        if not segments:
            raise RuntimeError(
                f"preprocessing_job {job_row.preprocessing_job_id} has no segments"
            )

        sentences = await _load_transcript_sentences_for_cos(
            db, job_row.cos_object_key
        )
        audio_unavailable = len(sentences) == 0

        # 4) LLM client（按需懒构造）
        llm_client = _make_llm_client_or_none()

        # 5) 取 coach_name + tech_category
        cls_row = (
            await db.execute(
                select(CoachVideoClassification).where(
                    CoachVideoClassification.id == job_row.coach_video_classification_id
                )
            )
        ).scalar_one_or_none()
        coach_name = cls_row.coach_name if cls_row else None
        tech_category = cls_row.tech_category if cls_row else "unclassified"

        # 6) 逐分段决策
        decisions: list[DecisionResult] = []
        for seg in segments:
            seg_text = extract_segment_text(
                sentences,
                segment_start_ms=seg.start_ms,
                segment_end_ms=seg.end_ms,
            )
            seg_duration = max(0, (seg.end_ms - seg.start_ms) / 1000.0)
            decisions.append(
                decide(
                    segment_text=seg_text,
                    rubric=rubric,
                    tech_category=tech_category,
                    coach_name=coach_name,
                    segment_duration_seconds=seg_duration,
                    llm_client=llm_client,
                )
            )

        # 7) 派生摘要 + 持久化
        summary = _aggregate_summary(segments, decisions, rubric, audio_unavailable)
        await _persist_results(
            db,
            job=job_row,
            segments=segments,
            decisions=decisions,
            summary=summary,
        )

        return "success"
    except AppException as exc:
        await db.rollback()
        await _mark_job_failed(db, job_id, exc.code.value, exc.message)
        return "failed"
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_curation_job crashed: job_id=%s", job_id)
        await db.rollback()
        await _mark_job_failed(
            db,
            job_id,
            ErrorCode.INTERNAL_ERROR.value,
            f"{type(exc).__name__}: {exc}"[:2000],
        )
        return "failed"


async def _mark_job_failed(
    db: AsyncSession,
    job_id: uuid.UUID,
    error_code: str,
    error_message: str,
) -> None:
    try:
        await db.execute(
            update(VideoCurationJob)
            .where(VideoCurationJob.id == job_id)
            .values(
                status="failed",
                completed_at=now_cst(),
                error_code=error_code[:64],
                error_message=error_message[:2000],
            )
        )
        await db.commit()
    except Exception:
        logger.exception("failed to mark job %s as failed", job_id)


def _make_llm_client_or_none() -> Any | None:
    """懒构造 LLM 客户端；缺凭证时返回 None（决策路按 rubric.unavailable_decision 兜底）。"""
    try:
        from src.services.llm_client import LlmClient

        return LlmClient.from_settings()
    except Exception as exc:  # noqa: BLE001
        logger.info("LlmClient unavailable for curation: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────
# 查询入口（router GET / 任务监控用）
# ─────────────────────────────────────────────────────────────────────────


async def fetch_curation_job_with_segments(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    include_segments: bool = True,
) -> tuple[VideoCurationJob, list[VideoCurationSegmentResult], dict[str, Any]] | None:
    """供 GET /curation-jobs/{id} 路由使用。

    Returns:
        ``(job, segments, extras)`` 或 ``None``（不存在）。``extras`` 含
        ``has_overrides`` / ``kb_stale_after_override``，path-router 转 schema
        时直接读。
    """
    job_row = (
        await db.execute(
            select(VideoCurationJob).where(VideoCurationJob.id == job_id)
        )
    ).scalar_one_or_none()
    if job_row is None:
        return None

    segments: list[VideoCurationSegmentResult] = []
    if include_segments:
        seg_stmt = (
            select(VideoCurationSegmentResult)
            .where(VideoCurationSegmentResult.job_id == job_id)
            .order_by(VideoCurationSegmentResult.segment_index.asc())
        )
        segments = list((await db.execute(seg_stmt)).scalars().all())

    has_overrides_count = (
        await db.execute(
            select(func.count())
            .select_from(VideoCurationSegmentResult)
            .where(
                VideoCurationSegmentResult.job_id == job_id,
                VideoCurationSegmentResult.override_decision.isnot(None),
            )
        )
    ).scalar_one()
    has_overrides = bool(has_overrides_count)

    kb_stale_stmt = select(CoachVideoClassification.kb_stale_after_override).where(
        CoachVideoClassification.id == job_row.coach_video_classification_id
    )
    kb_stale_after_override = bool(
        (await db.execute(kb_stale_stmt)).scalar_one_or_none()
    )

    return job_row, segments, {
        "has_overrides": has_overrides,
        "kb_stale_after_override": kb_stale_after_override,
    }


__all__ = [
    "CurationSubmissionOutcome",
    "CurationBatchSubmittedItem",
    "CurationBatchRejectedItem",
    "CurationBatchOutcome",
    "submit_curation",
    "submit_curation_batch",
    "run_curation_job",
    "fetch_curation_job_with_segments",
]
