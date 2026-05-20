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

from sqlalchemy import case, func, select, update
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


@dataclass
class OverrideOutcome:
    """单分段人工覆盖结果，router 转 Pydantic 响应。"""

    job_id: uuid.UUID
    segment_index: int
    auto_decision: str
    override_decision: str | None
    override_user: str | None
    override_reason: str | None
    overridden_at: datetime | None
    effective_decision: str
    summary_recomputed: dict[str, Any]
    kb_stale_after_override: bool


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


# ─────────────────────────────────────────────────────────────────────────
# 人工覆盖（US4）
# ─────────────────────────────────────────────────────────────────────────


_VALID_OVERRIDE_DECISIONS = ("accepted", "rejected")


async def override_segment(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    segment_index: int,
    override_decision: str | None,
    override_reason: str | None,
    override_user: str,
) -> OverrideOutcome:
    """对单分段人工覆盖 / 取消覆盖.

    单事务内：
      1. 校验 override_decision ∈ {accepted, rejected, None}（None 表"取消覆盖"）
      2. 校验 ``video_curation_jobs.status='success'``（其它状态 ⇒ INVALID_STATE）
      3. 校验目标分段存在
      4. UPDATE segment row（覆盖：写 override_*；取消：清空所有 override 字段）
      5. 重新加载所有分段 → 由清洗规范派生 ``low_quality`` 阈值 → 重算视频级摘要
      6. UPDATE ``video_curation_jobs`` 摘要字段
      7. 维护 ``coach_video_classifications.low_quality / kb_stale_after_override``
         （后者取决于是否存在 ``extraction_jobs`` completed 早于任何 overridden_at）

    Raises:
        AppException(VALIDATION_FAILED): override_decision / override_user 非法
        AppException(NOT_FOUND): 作业 / 分段不存在
        AppException(INVALID_STATE): 作业未完成
    """
    # 1) 输入校验（router 已用 Pydantic 拦了大部分；此处兜底"取消覆盖"语义）
    if override_decision is not None and override_decision not in _VALID_OVERRIDE_DECISIONS:
        raise AppException(
            ErrorCode.VALIDATION_FAILED,
            message=f"override_decision must be one of {_VALID_OVERRIDE_DECISIONS} or null",
            details={"override_decision": override_decision},
        )
    if not override_user or not override_user.strip():
        raise AppException(
            ErrorCode.VALIDATION_FAILED,
            message="override_user must be non-empty",
            details={"field": "override_user"},
        )
    # 覆盖时必须给 reason；取消覆盖时 reason 可为空
    if override_decision is not None:
        if not override_reason or not override_reason.strip():
            raise AppException(
                ErrorCode.VALIDATION_FAILED,
                message="override_reason is required when override_decision is set",
                details={"field": "override_reason"},
            )

    # 2) 加载作业
    job = (
        await db.execute(
            select(VideoCurationJob).where(VideoCurationJob.id == job_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise AppException(
            ErrorCode.NOT_FOUND,
            message="curation job not found",
            details={"resource_id": str(job_id)},
        )
    if job.status != "success":
        raise AppException(
            ErrorCode.INVALID_STATUS,
            message=f"cannot override segments on job with status={job.status!r}",
            details={"job_id": str(job_id), "status": job.status},
        )

    # 3) 加载目标分段
    seg_row = (
        await db.execute(
            select(VideoCurationSegmentResult).where(
                VideoCurationSegmentResult.job_id == job_id,
                VideoCurationSegmentResult.segment_index == segment_index,
            )
        )
    ).scalar_one_or_none()
    if seg_row is None:
        raise AppException(
            ErrorCode.NOT_FOUND,
            message="segment not found in this curation job",
            details={"job_id": str(job_id), "segment_index": segment_index},
        )

    # 4) UPDATE segment row（覆盖 vs 取消覆盖）
    now = now_cst()
    if override_decision is None:
        # 取消覆盖：清空所有 override 字段
        update_values: dict[str, Any] = {
            "override_decision": None,
            "override_user": None,
            "override_reason": None,
            "overridden_at": None,
            "updated_at": now,
        }
    else:
        update_values = {
            "override_decision": override_decision,
            "override_user": override_user,
            "override_reason": override_reason,
            "overridden_at": now,
            "updated_at": now,
        }
    await db.execute(
        update(VideoCurationSegmentResult)
        .where(VideoCurationSegmentResult.id == seg_row.id)
        .values(**update_values)
    )

    # 5) 重新加载所有分段以重算摘要（覆盖后 effective_decision 由 STORED 列自动同步）
    all_segs = (
        await db.execute(
            select(VideoCurationSegmentResult)
            .where(VideoCurationSegmentResult.job_id == job_id)
            .order_by(VideoCurationSegmentResult.segment_index.asc())
        )
    ).scalars().all()

    # 该作业关联的预处理分段（提供 start_ms/end_ms 与 segment_index 对齐）
    pp_segs = await _load_segments_for_job(db, job.preprocessing_job_id)
    pp_by_idx = {s.segment_index: s for s in pp_segs}

    rubric = rubric_loader.load(job.curation_rubric_version)
    audio_unavailable_value = bool(job.audio_unavailable) if job.audio_unavailable is not None else False

    # 用 effective_decision 重算（accepted / rejected / uncertain 三档计数）
    accepted_count = 0
    rejected_count = 0
    uncertain_count = 0
    accepted_duration_ms = 0
    total_duration_ms = 0
    for seg in all_segs:
        pp_seg = pp_by_idx.get(seg.segment_index)
        if pp_seg is None:
            # 极端：分段索引漂移（force=true 重切预处理）—— 跳过
            continue
        seg_dur_ms = max(0, pp_seg.end_ms - pp_seg.start_ms)
        total_duration_ms += seg_dur_ms
        eff = seg.effective_decision
        if eff == "accepted":
            accepted_count += 1
            accepted_duration_ms += seg_dur_ms
        elif eff == "rejected":
            rejected_count += 1
        else:
            uncertain_count += 1

    total_duration_seconds = total_duration_ms / 1000.0
    accepted_duration_seconds = accepted_duration_ms / 1000.0
    ratio = (
        accepted_duration_seconds / total_duration_seconds
        if total_duration_seconds > 0
        else 0.0
    )
    low_quality = ratio < rubric.low_quality_ratio
    short_video = total_duration_seconds < float(rubric.short_video_seconds)

    summary = {
        "total_segment_count": len(all_segs),
        "accepted_segment_count": accepted_count,
        "rejected_segment_count": rejected_count,
        "uncertain_segment_count": uncertain_count,
        "total_duration_seconds": round(total_duration_seconds, 3),
        "accepted_duration_seconds": round(accepted_duration_seconds, 3),
        "accepted_duration_ratio": round(ratio, 4),
        "low_quality": low_quality,
        "audio_unavailable": audio_unavailable_value,
        "short_video": short_video,
    }

    # 6) UPDATE 作业摘要
    await db.execute(
        update(VideoCurationJob)
        .where(VideoCurationJob.id == job_id)
        .values(updated_at=now, **summary)
    )

    # 7) 维护 coach_video_classifications.low_quality + kb_stale_after_override
    #    kb_stale 判定：是否存在已完成的 extraction_jobs 早于"任何分段的 overridden_at"
    kb_stale = await _evaluate_kb_stale_after_override(
        db,
        cos_object_key=job.cos_object_key,
        coach_video_classification_id=job.coach_video_classification_id,
    )

    await db.execute(
        update(CoachVideoClassification)
        .where(CoachVideoClassification.id == job.coach_video_classification_id)
        .values(
            low_quality=low_quality,
            kb_stale_after_override=kb_stale,
        )
    )

    await db.commit()

    # 重新读分段拿到最新 effective_decision（计算列）+ overridden_at
    updated_seg = (
        await db.execute(
            select(VideoCurationSegmentResult).where(
                VideoCurationSegmentResult.job_id == job_id,
                VideoCurationSegmentResult.segment_index == segment_index,
            )
        )
    ).scalar_one()

    return OverrideOutcome(
        job_id=job_id,
        segment_index=segment_index,
        auto_decision=updated_seg.auto_decision,
        override_decision=updated_seg.override_decision,
        override_user=updated_seg.override_user,
        override_reason=updated_seg.override_reason,
        overridden_at=updated_seg.overridden_at,
        effective_decision=updated_seg.effective_decision,
        summary_recomputed=summary,
        kb_stale_after_override=kb_stale,
    )


async def _evaluate_kb_stale_after_override(
    db: AsyncSession,
    *,
    cos_object_key: str,
    coach_video_classification_id: uuid.UUID,
) -> bool:
    """计算 kb_stale_after_override：

    True 当且仅当：
      - 该视频存在至少一条 ``video_curation_segment_results.overridden_at IS NOT NULL``
      - **且** 存在一条 ``extraction_jobs`` ``status='success'`` 完成于
        最早一条 ``overridden_at`` *之后或之前任意时间*——即任何已落地的 KB 抽取
        都基于"覆盖前"口径

    简化判定（与 router 监控语义对齐）：
      ``EXISTS extraction_jobs WHERE cos_object_key = ? AND status = 'success' AND
      completed_at < (MAX overridden_at over all segments of this video's curation jobs)``

    无覆盖记录或无成功 KB 作业 ⇒ False。
    """
    from src.models.extraction_job import ExtractionJob, ExtractionJobStatus

    # 该 classification 关联的所有 curation_jobs.id 集合
    job_ids_rows = (
        await db.execute(
            select(VideoCurationJob.id).where(
                VideoCurationJob.coach_video_classification_id
                == coach_video_classification_id
            )
        )
    ).all()
    job_ids = [r[0] for r in job_ids_rows]
    if not job_ids:
        return False

    # 取最早一条 overridden_at（如果有覆盖）
    latest_override_at = (
        await db.execute(
            select(func.max(VideoCurationSegmentResult.overridden_at)).where(
                VideoCurationSegmentResult.job_id.in_(job_ids),
                VideoCurationSegmentResult.overridden_at.isnot(None),
            )
        )
    ).scalar_one_or_none()
    if latest_override_at is None:
        return False

    # 是否存在已完成的 KB 作业 completed_at < latest_override_at
    stale_exists = (
        await db.execute(
            select(func.count())
            .select_from(ExtractionJob)
            .where(
                ExtractionJob.cos_object_key == cos_object_key,
                ExtractionJob.status == ExtractionJobStatus.success,
                ExtractionJob.completed_at.isnot(None),
                ExtractionJob.completed_at < latest_override_at,
            )
        )
    ).scalar_one()

    return bool(stale_exists)


async def clear_kb_stale_after_override(
    db: AsyncSession,
    *,
    cos_object_key: str,
) -> None:
    """``POST /extraction-jobs/{id}/rerun`` 完成后调用：清零 kb_stale_after_override.

    服务层暴露此函数以让既有 extraction_jobs router 在 rerun success 处加副作用。
    幂等；多次调用安全。
    """
    await db.execute(
        update(CoachVideoClassification)
        .where(CoachVideoClassification.cos_object_key == cos_object_key)
        .values(kb_stale_after_override=False)
    )


# ─────────────────────────────────────────────────────────────────────────
# 聚合统计（US5 — GET /curation-stats）
# ─────────────────────────────────────────────────────────────────────────


_VALID_GROUP_BY = ("coach", "tech_category", "rubric_version")
_LOW_SAMPLE_THRESHOLD = 5


@dataclass
class CurationStatsItemDTO:
    """聚合项 DTO（router 转 Pydantic 响应）.

    根据 ``group_by`` 不同，``coach_name`` / ``tech_category`` /
    ``curation_rubric_version`` 三字段按需置 None。
    """

    coach_name: str | None
    tech_category: str | None
    curation_rubric_version: str | None
    video_count: int
    avg_accepted_duration_ratio: float | None
    avg_validity_score: float | None
    low_quality_video_count: int
    with_overrides_video_count: int | None
    low_sample: bool


async def aggregate_curation_stats(
    db: AsyncSession,
    *,
    group_by: str,
    coach_name: str | None = None,
    tech_category: str | None = None,
    rubric_version: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CurationStatsItemDTO], int]:
    """跨教练 / 类别 / 规范版本的有效率聚合（spec FR-013，US5）.

    数据源：``video_curation_jobs`` JOIN ``coach_video_classifications`` —— 仅
    统计 ``video_curation_jobs.status='success'`` 的作业（已完成口径）。

    参数互斥语义（contracts/curation_stats.md § 行为契约 3）：
        - ``group_by=coach`` 时 ``coach_name`` 仍可作为 WHERE 过滤（语义"按指定教练单条返回"）
        - ``group_by=tech_category`` / ``rubric_version`` 同理
        路由层 Pydantic 已拦了 group_by 枚举与分页越界。

    Returns:
        ``(items, total)``：``items`` 为当前页聚合结果，``total`` 为分组数。
    """
    if group_by not in _VALID_GROUP_BY:
        raise AppException(
            ErrorCode.VALIDATION_FAILED,
            message=f"group_by must be one of {_VALID_GROUP_BY}",
            details={"field": "group_by", "value": group_by},
        )

    # 三种分组键映射到 SQL 表达式 + Python 字段名
    if group_by == "coach":
        group_col = CoachVideoClassification.coach_name
        group_label = "coach_name"
    elif group_by == "tech_category":
        group_col = CoachVideoClassification.tech_category
        group_label = "tech_category"
    else:  # rubric_version
        group_col = VideoCurationJob.curation_rubric_version
        group_label = "curation_rubric_version"

    # 子查询：每条视频取最近一条 success 作业（按 cos_object_key + classification 唯一）
    # 简化口径：以 video_curation_jobs.id 为粒度统计；同一视频多次成功重跑会被分别计入。
    # 与 spec § FR-013 "整体口径"一致——文档未要求"按视频去重"，按 job 行做均值更稳定。
    join_stmt = select(
        group_col.label("group_key"),
        VideoCurationJob.id.label("job_id"),
        VideoCurationJob.cos_object_key,
        VideoCurationJob.coach_video_classification_id,
        VideoCurationJob.accepted_duration_ratio,
        VideoCurationJob.low_quality,
    ).join(
        CoachVideoClassification,
        CoachVideoClassification.id
        == VideoCurationJob.coach_video_classification_id,
    ).where(
        VideoCurationJob.status == "success",
    )

    if coach_name:
        join_stmt = join_stmt.where(CoachVideoClassification.coach_name == coach_name)
    if tech_category:
        join_stmt = join_stmt.where(
            CoachVideoClassification.tech_category == tech_category
        )
    if rubric_version:
        join_stmt = join_stmt.where(
            VideoCurationJob.curation_rubric_version == rubric_version
        )

    base_subq = join_stmt.subquery()

    # 分组聚合：每组内 video_count / avg_ratio / low_quality_count
    agg_stmt = select(
        base_subq.c.group_key,
        func.count(base_subq.c.job_id).label("video_count"),
        func.avg(base_subq.c.accepted_duration_ratio).label("avg_ratio"),
        func.sum(
            case(
                (base_subq.c.low_quality.is_(True), 1),
                else_=0,
            )
        ).label("low_quality_video_count"),
    ).where(
        base_subq.c.group_key.isnot(None),
    ).group_by(base_subq.c.group_key).order_by(base_subq.c.group_key.asc())

    # 总分组数（分页 total）
    total_stmt = select(func.count()).select_from(agg_stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one())

    # 分页
    paged_stmt = agg_stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(paged_stmt)).all()

    if not rows:
        return [], total

    group_keys = [r.group_key for r in rows]

    # 二阶段：每组的 avg_validity_score（以分段为权）
    seg_score_stmt = (
        select(
            group_col.label("group_key"),
            func.avg(VideoCurationSegmentResult.validity_score).label("avg_score"),
        )
        .select_from(VideoCurationSegmentResult)
        .join(
            VideoCurationJob,
            VideoCurationJob.id == VideoCurationSegmentResult.job_id,
        )
        .join(
            CoachVideoClassification,
            CoachVideoClassification.id
            == VideoCurationJob.coach_video_classification_id,
        )
        .where(
            VideoCurationJob.status == "success",
            group_col.in_(group_keys),
        )
    )
    if coach_name:
        seg_score_stmt = seg_score_stmt.where(
            CoachVideoClassification.coach_name == coach_name
        )
    if tech_category:
        seg_score_stmt = seg_score_stmt.where(
            CoachVideoClassification.tech_category == tech_category
        )
    if rubric_version:
        seg_score_stmt = seg_score_stmt.where(
            VideoCurationJob.curation_rubric_version == rubric_version
        )
    seg_score_stmt = seg_score_stmt.group_by(group_col)
    avg_score_by_group: dict[Any, float | None] = {
        r.group_key: (float(r.avg_score) if r.avg_score is not None else None)
        for r in (await db.execute(seg_score_stmt)).all()
    }

    # 三阶段：每组含覆盖记录的 distinct 视频数（仅 coach / tech_category 维度有意义；
    # rubric_version 维度按 contracts 示例不给该字段）
    with_overrides_by_group: dict[Any, int] = {}
    if group_by in ("coach", "tech_category"):
        ov_stmt = (
            select(
                group_col.label("group_key"),
                func.count(func.distinct(VideoCurationJob.id)).label("with_ov"),
            )
            .select_from(VideoCurationSegmentResult)
            .join(
                VideoCurationJob,
                VideoCurationJob.id == VideoCurationSegmentResult.job_id,
            )
            .join(
                CoachVideoClassification,
                CoachVideoClassification.id
                == VideoCurationJob.coach_video_classification_id,
            )
            .where(
                VideoCurationJob.status == "success",
                VideoCurationSegmentResult.override_decision.isnot(None),
                group_col.in_(group_keys),
            )
        )
        if coach_name:
            ov_stmt = ov_stmt.where(CoachVideoClassification.coach_name == coach_name)
        if tech_category:
            ov_stmt = ov_stmt.where(
                CoachVideoClassification.tech_category == tech_category
            )
        if rubric_version:
            ov_stmt = ov_stmt.where(
                VideoCurationJob.curation_rubric_version == rubric_version
            )
        ov_stmt = ov_stmt.group_by(group_col)
        with_overrides_by_group = {
            r.group_key: int(r.with_ov)
            for r in (await db.execute(ov_stmt)).all()
        }

    # 拼接 DTO + 附 low_sample 标记
    items: list[CurationStatsItemDTO] = []
    for r in rows:
        gkey = r.group_key
        video_count = int(r.video_count or 0)
        avg_ratio = float(r.avg_ratio) if r.avg_ratio is not None else None
        avg_score = avg_score_by_group.get(gkey)
        low_q_count = int(r.low_quality_video_count or 0)

        item = CurationStatsItemDTO(
            coach_name=gkey if group_label == "coach_name" else None,
            tech_category=gkey if group_label == "tech_category" else None,
            curation_rubric_version=gkey
            if group_label == "curation_rubric_version"
            else None,
            video_count=video_count,
            avg_accepted_duration_ratio=(
                round(avg_ratio, 4) if avg_ratio is not None else None
            ),
            avg_validity_score=(
                round(avg_score, 4) if avg_score is not None else None
            ),
            low_quality_video_count=low_q_count,
            with_overrides_video_count=(
                with_overrides_by_group.get(gkey, 0)
                if group_by in ("coach", "tech_category")
                else None
            ),
            low_sample=video_count < _LOW_SAMPLE_THRESHOLD,
        )
        items.append(item)

    return items, total


__all__ = [
    "CurationSubmissionOutcome",
    "CurationBatchSubmittedItem",
    "CurationBatchRejectedItem",
    "CurationBatchOutcome",
    "OverrideOutcome",
    "CurationStatsItemDTO",
    "submit_curation",
    "submit_curation_batch",
    "run_curation_job",
    "fetch_curation_job_with_segments",
    "override_segment",
    "clear_kb_stale_after_override",
    "aggregate_curation_stats",
]
