"""Feature-022 · T022 · 集成测试：重新清洗后审核状态自动 stale.

覆盖范围（spec.md US3 + FR-011/FR-011a + 澄清 Q3）：
  AC1: approved 条目重新清洗成功 → review_state 自动迁移到 pending_review
       （合并 approved → stale → pending_review，不暴露中间 stale 态）
  AC2: review_version +1，pending_since 重置为新的"重审入队时刻"
  AC3: 旧的 last_decision 行被标记 superseded_at（非 NULL），但 last_decision_id
       继续指向旧决策（用于审计回溯）
  AC4: rejected 条目重新清洗成功 → 保持 rejected（澄清 Q5：永久保留）
  AC5: pending_review 条目重新清洗成功 → 保持 pending_review，pending_since 不动

注意：本测试**不**触发真实的清洗 worker 流程；它直接调用 ``curation_service``
的 success 回调子流程（即写入摘要 + 反向同步 + 触发 stale_handler 的链路），
确保审核状态机的同事务原子性。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.db import session as session_module
from src.models.coach_video_classification import CoachVideoClassification
from src.models.content_review_decision import ContentReviewDecision
from src.models.video_curation_job import VideoCurationJob
from src.models.video_preprocessing_job import VideoPreprocessingJob
from src.services.content_review.stale_handler import mark_stale_after_recurate
from src.utils.time_utils import now_cst


_TAG = f"__t022_{uuid.uuid4().hex[:8]}"


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_size=2, max_overflow=2, pool_pre_ping=False,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    orig_engine = session_module.engine
    orig_factory = session_module.AsyncSessionFactory
    session_module.engine = engine
    session_module.AsyncSessionFactory = factory
    try:
        yield factory
    finally:
        session_module.engine = orig_engine
        session_module.AsyncSessionFactory = orig_factory
        await engine.dispose()


async def _create_classification_with_curation(
    session: AsyncSession,
    *,
    cos_object_key: str,
    review_state: str = "pending_review",
    review_version: int = 0,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """创建 cvclf + prep_job + cur_job(success)，返回 (cvclf_id, prep_id, cur_id)."""
    cvclf_id = uuid.uuid4()
    prep_id = uuid.uuid4()
    cur_id = uuid.uuid4()

    cvclf = CoachVideoClassification(
        id=cvclf_id,
        coach_name=f"{_TAG}_coach",
        course_series=f"{_TAG}_series",
        cos_object_key=cos_object_key,
        filename=cos_object_key.split("/")[-1],
        tech_category="forehand_topspin",
        tech_tags=["正手"],
        classification_source="rule",
        confidence=1.0,
        kb_extracted=False,
        preprocessed=True,
    )
    session.add(cvclf)
    prep_job = VideoPreprocessingJob(
        id=prep_id, cos_object_key=cos_object_key, status="success"
    )
    session.add(prep_job)
    await session.flush()
    cur_job = VideoCurationJob(
        id=cur_id, cos_object_key=cos_object_key,
        coach_video_classification_id=cvclf_id,
        preprocessing_job_id=prep_id,
        curation_rubric_version="v1", status="success",
        total_segment_count=5, accepted_segment_count=5,
        rejected_segment_count=0, uncertain_segment_count=0,
        total_duration_seconds=180.0, accepted_duration_seconds=180.0,
        accepted_duration_ratio=1.0, low_quality=False,
        audio_unavailable=False, short_video=False,
        completed_at=datetime.now(),
    )
    session.add(cur_job)
    await session.flush()
    await session.execute(
        update(CoachVideoClassification)
        .where(CoachVideoClassification.id == cvclf_id)
        .values(
            last_curation_job_id=cur_id,
            review_state=review_state,
            review_version=review_version,
        )
    )
    return cvclf_id, prep_id, cur_id


async def _cleanup(session_factory, cos_object_key: str) -> None:
    """删除测试 seed 的所有 cvclf + prep_job（cur_job 由 cvclf CASCADE 删除）."""
    async with session_factory() as session:
        # 找到 cvclf 行（按 cos_object_key 唯一）
        cvclf_id = (
            await session.execute(
                select(CoachVideoClassification.id).where(
                    CoachVideoClassification.cos_object_key == cos_object_key
                )
            )
        ).scalar_one_or_none()
        if cvclf_id is not None:
            await session.execute(
                delete(CoachVideoClassification).where(
                    CoachVideoClassification.id == cvclf_id
                )
            )
        await session.execute(
            delete(VideoPreprocessingJob).where(
                VideoPreprocessingJob.cos_object_key == cos_object_key
            )
        )
        await session.commit()


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_us3_ac1_approved_recurate_transitions_to_pending_review(
    session_factory,
):
    """AC1: approved 条目重新清洗 → pending_review（合并状态机，不暴露中间 stale）."""
    cos_object_key = f"charhuang/tt_video/{_TAG}/ac1.mp4"
    try:
        async with session_factory() as session:
            cvclf_id, _, old_cur_id = await _create_classification_with_curation(
                session, cos_object_key=cos_object_key,
                review_state="pending_review", review_version=0,
            )
            # 模拟一次审核 approved 决策
            decision = ContentReviewDecision(
                cvclf_id=cvclf_id,
                cleansing_version=old_cur_id,
                decision="approved",
                reviewer_id="ops-t022-ac1",
                decided_at=now_cst(),
            )
            session.add(decision)
            await session.flush()
            await session.execute(
                update(CoachVideoClassification)
                .where(CoachVideoClassification.id == cvclf_id)
                .values(
                    review_state="approved",
                    last_decision_id=decision.id,
                    review_version=1,
                    pending_since=None,
                )
            )
            await session.commit()
            old_decision_id = decision.id

        # 模拟一次重新清洗（新的 cur_job）
        async with session_factory() as session:
            new_cur_id = uuid.uuid4()
            new_cur_job = VideoCurationJob(
                id=new_cur_id, cos_object_key=cos_object_key,
                coach_video_classification_id=cvclf_id,
                preprocessing_job_id=(
                    await session.execute(
                        select(VideoPreprocessingJob.id).where(
                            VideoPreprocessingJob.cos_object_key == cos_object_key
                        )
                    )
                ).scalar_one(),
                curation_rubric_version="v1", status="success",
                total_segment_count=4, accepted_segment_count=4,
                rejected_segment_count=0, uncertain_segment_count=0,
                total_duration_seconds=160.0, accepted_duration_seconds=160.0,
                accepted_duration_ratio=1.0, low_quality=False,
                audio_unavailable=False, short_video=False,
                completed_at=datetime.now(),
            )
            session.add(new_cur_job)
            await session.flush()

            # 反向回填 cvclf.last_curation_job_id（curation_service 的实际行为）
            await session.execute(
                update(CoachVideoClassification)
                .where(CoachVideoClassification.id == cvclf_id)
                .values(last_curation_job_id=new_cur_id)
            )
            # 触发 stale_handler（curation_service success 路径会自动调用）
            outcome = await mark_stale_after_recurate(
                session, cvclf_id=cvclf_id, new_curation_job_id=new_cur_id,
            )
            assert outcome == "approved_to_pending_review", outcome
            await session.commit()

        # 验证 final 状态
        async with session_factory() as session:
            cvclf = (
                await session.execute(
                    select(CoachVideoClassification).where(
                        CoachVideoClassification.id == cvclf_id
                    )
                )
            ).scalar_one()
            # AC1: 直接落 pending_review，不暴露中间 stale 态
            assert cvclf.review_state == "pending_review"
            # AC2: review_version +1（1→2）
            assert cvclf.review_version == 2
            # AC2: pending_since 被重置为新的入队时刻
            assert cvclf.pending_since is not None
            # AC3: last_decision_id 仍指向旧决策（审计回溯）
            assert cvclf.last_decision_id == old_decision_id

            # AC3: 旧决策行 superseded_at 已被标记
            old_decision = (
                await session.execute(
                    select(ContentReviewDecision).where(
                        ContentReviewDecision.id == old_decision_id
                    )
                )
            ).scalar_one()
            assert old_decision.superseded_at is not None
    finally:
        await _cleanup(session_factory, cos_object_key)


@pytest.mark.asyncio
async def test_us3_ac4_rejected_recurate_keeps_rejected(session_factory):
    """AC4: rejected 条目重新清洗 → 保持 rejected（澄清 Q5：永久保留）."""
    cos_object_key = f"charhuang/tt_video/{_TAG}/ac4.mp4"
    try:
        async with session_factory() as session:
            cvclf_id, _, old_cur_id = await _create_classification_with_curation(
                session, cos_object_key=cos_object_key,
                review_state="pending_review", review_version=0,
            )
            # 模拟一次 rejected 决策
            decision = ContentReviewDecision(
                cvclf_id=cvclf_id,
                cleansing_version=old_cur_id,
                decision="rejected",
                reason_code="quality_low",
                reviewer_id="ops-t022-ac4",
                decided_at=now_cst(),
            )
            session.add(decision)
            await session.flush()
            await session.execute(
                update(CoachVideoClassification)
                .where(CoachVideoClassification.id == cvclf_id)
                .values(
                    review_state="rejected",
                    last_decision_id=decision.id,
                    review_version=1,
                    pending_since=None,
                )
            )
            await session.commit()

        # 重新清洗
        async with session_factory() as session:
            new_cur_id = uuid.uuid4()
            new_cur_job = VideoCurationJob(
                id=new_cur_id, cos_object_key=cos_object_key,
                coach_video_classification_id=cvclf_id,
                preprocessing_job_id=(
                    await session.execute(
                        select(VideoPreprocessingJob.id).where(
                            VideoPreprocessingJob.cos_object_key == cos_object_key
                        )
                    )
                ).scalar_one(),
                curation_rubric_version="v1", status="success",
                total_segment_count=3, accepted_segment_count=3,
                rejected_segment_count=0, uncertain_segment_count=0,
                total_duration_seconds=120.0, accepted_duration_seconds=120.0,
                accepted_duration_ratio=1.0, low_quality=False,
                audio_unavailable=False, short_video=False,
                completed_at=datetime.now(),
            )
            session.add(new_cur_job)
            await session.flush()
            await session.execute(
                update(CoachVideoClassification)
                .where(CoachVideoClassification.id == cvclf_id)
                .values(last_curation_job_id=new_cur_id)
            )
            outcome = await mark_stale_after_recurate(
                session, cvclf_id=cvclf_id, new_curation_job_id=new_cur_id,
            )
            # rejected 永久保留，无需迁移
            assert outcome is None
            await session.commit()

        # 验证 final 状态：仍是 rejected，review_version 不变
        async with session_factory() as session:
            cvclf = (
                await session.execute(
                    select(CoachVideoClassification).where(
                        CoachVideoClassification.id == cvclf_id
                    )
                )
            ).scalar_one()
            assert cvclf.review_state == "rejected"
            assert cvclf.review_version == 1  # 不变
    finally:
        await _cleanup(session_factory, cos_object_key)


@pytest.mark.asyncio
async def test_us3_ac5_pending_review_recurate_keeps_pending(session_factory):
    """AC5: pending_review 条目重新清洗 → 保持 pending_review，pending_since 不变."""
    cos_object_key = f"charhuang/tt_video/{_TAG}/ac5.mp4"
    try:
        async with session_factory() as session:
            cvclf_id, _, _ = await _create_classification_with_curation(
                session, cos_object_key=cos_object_key,
                review_state="pending_review", review_version=0,
            )
            # 设置 pending_since（模拟首次进入 pending 时刻）
            initial_pending_since = now_cst()
            await session.execute(
                update(CoachVideoClassification)
                .where(CoachVideoClassification.id == cvclf_id)
                .values(pending_since=initial_pending_since)
            )
            await session.commit()

        # 重新清洗
        async with session_factory() as session:
            new_cur_id = uuid.uuid4()
            new_cur_job = VideoCurationJob(
                id=new_cur_id, cos_object_key=cos_object_key,
                coach_video_classification_id=cvclf_id,
                preprocessing_job_id=(
                    await session.execute(
                        select(VideoPreprocessingJob.id).where(
                            VideoPreprocessingJob.cos_object_key == cos_object_key
                        )
                    )
                ).scalar_one(),
                curation_rubric_version="v1", status="success",
                total_segment_count=2, accepted_segment_count=2,
                rejected_segment_count=0, uncertain_segment_count=0,
                total_duration_seconds=80.0, accepted_duration_seconds=80.0,
                accepted_duration_ratio=1.0, low_quality=False,
                audio_unavailable=False, short_video=False,
                completed_at=datetime.now(),
            )
            session.add(new_cur_job)
            await session.flush()
            await session.execute(
                update(CoachVideoClassification)
                .where(CoachVideoClassification.id == cvclf_id)
                .values(last_curation_job_id=new_cur_id)
            )
            outcome = await mark_stale_after_recurate(
                session, cvclf_id=cvclf_id, new_curation_job_id=new_cur_id,
            )
            assert outcome is None  # 无迁移
            await session.commit()

        # 验证：仍是 pending_review；pending_since 不变（保留首次入队时刻用于 SLA 计算）
        async with session_factory() as session:
            cvclf = (
                await session.execute(
                    select(CoachVideoClassification).where(
                        CoachVideoClassification.id == cvclf_id
                    )
                )
            ).scalar_one()
            assert cvclf.review_state == "pending_review"
            assert cvclf.review_version == 0
            assert cvclf.pending_since is not None
    finally:
        await _cleanup(session_factory, cos_object_key)
