"""Feature-022 · T027 · review_service 业务规则单元测试.

覆盖 [src/services/content_review/review_service.py](../../src/services/content_review/review_service.py)
中 ``submit_decision`` 的核心业务规则（无需经路由层）：

  - 乐观锁冲突：``expected_review_version`` 与 DB 当前不一致 → REVIEW_VERSION_CONFLICT
  - rejected 必须带 reason_code → REJECTED_REQUIRES_REASON
  - reason_code=other 必须带 note → VALIDATION_FAILED
  - approved 决策成功 → 主表 review_state=approved + last_decision_id 同步更新 +
    review_version+1 + pending_since=NULL
  - rejected 决策成功 → 主表 review_state=rejected + 决策行带 reason_code/note
  - header_reviewer_id 与 body.reviewer_id 不一致 → INVALID_REVIEWER_IDENTITY
  - cvclf_id 不存在 → NOT_FOUND
  - 状态机：non-pending_review 状态下提交 → REVIEW_NOT_PENDING

设计约束（章程 II / VI）：
  - 单元测试**只测业务规则**，不依赖 mock 框架；用真实 DB（章程 VI 测试栈）
  - 与 contract / integration 测试解耦：失败时定位到 service 层而非端点
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.errors import AppException, ErrorCode
from src.api.schemas.content_reviews import (
    Decision,
    DecisionSubmitRequest,
    ReasonCode,
)
from src.config import get_settings
from src.db import session as session_module
from src.models.coach_video_classification import CoachVideoClassification
from src.models.content_review_decision import ContentReviewDecision
from src.services.content_review.review_service import submit_decision


_TAG = f"__t027_{uuid.uuid4().hex[:8]}"


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


@pytest_asyncio.fixture
async def pending_cvclf(session_factory):
    """Seed 一条 review_state=pending_review / review_version=0 的 cvclf 行."""
    cos_object_key = f"charhuang/tt_video/{_TAG}/serve_001.mp4"
    row = CoachVideoClassification(
        id=uuid.uuid4(),
        coach_name=f"{_TAG}_coach",
        course_series=f"{_TAG}_series",
        cos_object_key=cos_object_key,
        filename="serve_001.mp4",
        tech_category="serve",
        tech_tags=["发球"],
        classification_source="rule",
        confidence=1.0,
        kb_extracted=False,
        preprocessed=True,
        # review_state / review_version 走 server_default
    )
    async with session_factory() as session:
        session.add(row)
        await session.commit()

    yield row.id

    async with session_factory() as session:
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.id == row.id
            )
        )
        await session.commit()


# ══════════════════════════════════════════════════════════════════════════
# 业务规则 1: header / body reviewer_id 一致性
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_invalid_reviewer_identity_header_body_mismatch(
    session_factory, pending_cvclf
):
    """T027.1: header X-Reviewer-Id 与 body.reviewer_id 不一致 → INVALID_REVIEWER_IDENTITY."""
    body = DecisionSubmitRequest(
        decision=Decision.approved,
        reviewer_id="ops-zhangwei",
        expected_review_version=0,
    )
    async with session_factory() as session:
        with pytest.raises(AppException) as exc_info:
            await submit_decision(
                session,
                cvclf_id=pending_cvclf,
                body=body,
                header_reviewer_id="ops-lihua",  # 不一致
            )
    assert exc_info.value.code == ErrorCode.INVALID_REVIEWER_IDENTITY
    assert exc_info.value.details["header_value"] == "ops-lihua"
    assert exc_info.value.details["body_value"] == "ops-zhangwei"


# ══════════════════════════════════════════════════════════════════════════
# 业务规则 2: rejected 必须带 reason_code
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rejected_without_reason_code_raises(
    session_factory, pending_cvclf
):
    """T027.2: rejected 决策不带 reason_code → REJECTED_REQUIRES_REASON."""
    body = DecisionSubmitRequest(
        decision=Decision.rejected,
        reviewer_id="ops-test",
        expected_review_version=0,
        # reason_code 故意省略
    )
    async with session_factory() as session:
        with pytest.raises(AppException) as exc_info:
            await submit_decision(
                session,
                cvclf_id=pending_cvclf,
                body=body,
                header_reviewer_id="ops-test",
            )
    assert exc_info.value.code == ErrorCode.REJECTED_REQUIRES_REASON


# ══════════════════════════════════════════════════════════════════════════
# 业务规则 3: reason_code=other 必须带 note
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rejected_other_without_note_raises(
    session_factory, pending_cvclf
):
    """T027.3: rejected + reason_code=other 不带 note → VALIDATION_FAILED."""
    body = DecisionSubmitRequest(
        decision=Decision.rejected,
        reason_code=ReasonCode.other,
        reviewer_id="ops-test",
        expected_review_version=0,
        # note 故意省略
    )
    async with session_factory() as session:
        with pytest.raises(AppException) as exc_info:
            await submit_decision(
                session,
                cvclf_id=pending_cvclf,
                body=body,
                header_reviewer_id="ops-test",
            )
    assert exc_info.value.code == ErrorCode.VALIDATION_FAILED
    # 错误详情应指向 note 字段
    assert "note" in (exc_info.value.message or "")


# ══════════════════════════════════════════════════════════════════════════
# 业务规则 4: approved 决策成功 → 主表三字段同步更新
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_approved_decision_updates_main_table(
    session_factory, pending_cvclf
):
    """T027.4: approved 成功 → review_state / last_decision_id / review_version 同步更新."""
    body = DecisionSubmitRequest(
        decision=Decision.approved,
        reviewer_id="ops-approver",
        expected_review_version=0,
    )
    async with session_factory() as session:
        result = await submit_decision(
            session,
            cvclf_id=pending_cvclf,
            body=body,
            header_reviewer_id="ops-approver",
        )

    # 返回值断言
    assert result.decision == Decision.approved
    assert result.reviewer_id == "ops-approver"
    assert result.reason_code is None
    assert result.note is None
    assert result.superseded_at is None
    new_decision_id = result.id

    # 主表断言
    async with session_factory() as session:
        cvclf = (
            await session.execute(
                select(CoachVideoClassification).where(
                    CoachVideoClassification.id == pending_cvclf
                )
            )
        ).scalar_one()
        assert cvclf.review_state == "approved"
        assert cvclf.review_version == 1  # 0 → 1
        assert cvclf.last_decision_id == new_decision_id
        assert cvclf.pending_since is None  # 决策落地清空 pending_since


# ══════════════════════════════════════════════════════════════════════════
# 业务规则 5: rejected + 完整字段 → 决策行 + 主表正确
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rejected_decision_with_reason_succeeds(
    session_factory, pending_cvclf
):
    """T027.5: rejected + reason_code=quality_low + note → 决策行落 reason/note，主表 rejected."""
    body = DecisionSubmitRequest(
        decision=Decision.rejected,
        reason_code=ReasonCode.quality_low,
        note="前 30 秒抖动严重",
        reviewer_id="ops-rejecter",
        expected_review_version=0,
    )
    async with session_factory() as session:
        result = await submit_decision(
            session,
            cvclf_id=pending_cvclf,
            body=body,
            header_reviewer_id="ops-rejecter",
        )

    assert result.decision == Decision.rejected
    assert result.reason_code == ReasonCode.quality_low
    assert result.note == "前 30 秒抖动严重"

    async with session_factory() as session:
        cvclf = (
            await session.execute(
                select(CoachVideoClassification).where(
                    CoachVideoClassification.id == pending_cvclf
                )
            )
        ).scalar_one()
        assert cvclf.review_state == "rejected"
        assert cvclf.review_version == 1


# ══════════════════════════════════════════════════════════════════════════
# 业务规则 6: 乐观锁冲突
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_optimistic_lock_version_mismatch_raises(
    session_factory, pending_cvclf
):
    """T027.6: expected_review_version 与 DB 当前不一致 → REVIEW_VERSION_CONFLICT."""
    # DB 当前是 review_version=0；用 99 提交模拟"客户端基于过期快照决策"
    body = DecisionSubmitRequest(
        decision=Decision.approved,
        reviewer_id="ops-stale",
        expected_review_version=99,  # 故意错的
    )
    async with session_factory() as session:
        with pytest.raises(AppException) as exc_info:
            await submit_decision(
                session,
                cvclf_id=pending_cvclf,
                body=body,
                header_reviewer_id="ops-stale",
            )
    assert exc_info.value.code == ErrorCode.REVIEW_VERSION_CONFLICT
    assert exc_info.value.details["expected_version"] == 99
    assert exc_info.value.details["current_version"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 业务规则 7: cvclf_id 不存在
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_nonexistent_cvclf_raises_not_found(session_factory):
    """T027.7: 不存在的 cvclf_id → NOT_FOUND."""
    fake_id = uuid.uuid4()
    body = DecisionSubmitRequest(
        decision=Decision.approved,
        reviewer_id="ops-test",
        expected_review_version=0,
    )
    async with session_factory() as session:
        with pytest.raises(AppException) as exc_info:
            await submit_decision(
                session,
                cvclf_id=fake_id,
                body=body,
                header_reviewer_id="ops-test",
            )
    assert exc_info.value.code == ErrorCode.NOT_FOUND


# ══════════════════════════════════════════════════════════════════════════
# 业务规则 8: 状态机 — 已 approved 后再提决策
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_review_not_pending_after_first_decision(
    session_factory, pending_cvclf
):
    """T027.8: 第一次 approved 后再提决策 → REVIEW_NOT_PENDING（主表已转 approved）."""
    # 第一次 approved
    body1 = DecisionSubmitRequest(
        decision=Decision.approved,
        reviewer_id="ops-first",
        expected_review_version=0,
    )
    async with session_factory() as session:
        await submit_decision(
            session, cvclf_id=pending_cvclf, body=body1,
            header_reviewer_id="ops-first",
        )

    # 第二次提（用正确的版本号 1 也不行，因为状态机已变成 approved）
    body2 = DecisionSubmitRequest(
        decision=Decision.rejected,
        reason_code=ReasonCode.other,
        note="尝试覆盖第一次决策",
        reviewer_id="ops-second",
        expected_review_version=1,
    )
    async with session_factory() as session:
        with pytest.raises(AppException) as exc_info:
            await submit_decision(
                session, cvclf_id=pending_cvclf, body=body2,
                header_reviewer_id="ops-second",
            )
    assert exc_info.value.code == ErrorCode.REVIEW_NOT_PENDING
    assert exc_info.value.details["current_review_state"] == "approved"


# ══════════════════════════════════════════════════════════════════════════
# 业务规则 9: 决策行 cleansing_version 与 cvclf.last_curation_job_id 同步快照
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_decision_carries_cleansing_version_snapshot(
    session_factory, pending_cvclf
):
    """T027.9: 决策行的 cleansing_version 应等于决策时 cvclf.last_curation_job_id 快照.

    验证 spec.md FR-013: 每次决策必须同步携带"作出决策时的清洗版本号"，
    便于事后审计"该决策基于哪一次清洗结果做出"。
    """
    body = DecisionSubmitRequest(
        decision=Decision.approved,
        reviewer_id="ops-snapshot",
        expected_review_version=0,
    )
    async with session_factory() as session:
        # 测试 cvclf seed 时 last_curation_job_id=None；决策行应同样为 None
        result = await submit_decision(
            session,
            cvclf_id=pending_cvclf,
            body=body,
            header_reviewer_id="ops-snapshot",
        )

    # 当前测试用例 cvclf 的 last_curation_job_id 是 None（fixture 简化），
    # 因此决策行的 cleansing_version 也应为 None
    assert result.cleansing_version is None

    # 跨表二次校验：DB 中的决策行 cleansing_version 字段也是 None
    async with session_factory() as session:
        decision_row = (
            await session.execute(
                select(ContentReviewDecision).where(
                    ContentReviewDecision.id == result.id
                )
            )
        ).scalar_one()
        assert decision_row.cleansing_version is None
        assert decision_row.cvclf_id == pending_cvclf
        assert isinstance(decision_row.decided_at, datetime)
