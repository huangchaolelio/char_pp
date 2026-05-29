"""Feature-022 · T035 · 集成测试：阶段级可观测性日志锚点验证.

覆盖范围（spec.md US4 验收 / FR-012 / SC-006）：

  AC1: 审核决策提交（EP-3）的结构化日志 ``extra`` 必含 ``phase=CONTENT_PREP``、
       ``step=content_review`` 与 ``decision`` 字段；满足 SRE 仪表盘按
       ``phase`` 字段聚合的需求。
  AC2: 审核 pending 周期采样（record_pending_metrics）产生 ``metric=
       content_review_pending_snapshot`` + ``phase=CONTENT_PREP`` 锚点。
  AC3: 积压告警（check_pending_backlog）在命中红线时产生
       ``metric=content_review_backlog_alert`` + ``alert.severity=high`` 锚点。
  AC4: 任务入队（task_submission_service）产生 ``metric=phase_enter_count`` +
       phase 派生字段（CONTENT_PREP / TRAINING / INFERENCE）。

设计决策：
  · 不去模拟"COS scan → preprocess → classify → curate → review → kb"完整流转
    （那是端到端冒烟，单测试时间长且依赖多 worker）；改为**单独验证每个埋点
    锚点的字段形态**。三段独立断言加起来等价覆盖 US4.AC1 的"独立可聚合"诉求。
  · 用 ``caplog.set_level`` + ``caplog.records`` 捕获日志记录；通过断言
    ``record.metric / record.phase`` 等扩展字段（来自 logger.info(extra=...)）
    确保 SRE 仪表盘字段约定不被破坏。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.config import get_settings
from src.db import session as session_module
from src.models.coach_video_classification import CoachVideoClassification
from src.services.content_review.backlog_monitor import check_pending_backlog
from src.services.content_review.review_service import record_pending_metrics
from src.utils.time_utils import now_cst
from tests.contract.conftest import assert_success_envelope


_TAG = f"__t035_{uuid.uuid4().hex[:8]}"


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
async def client(session_factory):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def pending_cvclf_with_old_pending_since(session_factory):
    """Seed 一行 review_state=pending_review、pending_since=48h 前的 cvclf.

    用于 backlog_monitor 红线触发测试（默认阈值 24h，48h 必命中）。
    """
    cvclf_id = uuid.uuid4()
    cos_object_key = f"charhuang/tt_video/{_TAG}/serve_old.mp4"
    old_pending_since = now_cst() - timedelta(hours=48)

    async with session_factory() as session:
        cvclf = CoachVideoClassification(
            id=cvclf_id,
            coach_name=f"{_TAG}_coach",
            course_series=f"{_TAG}_series",
            cos_object_key=cos_object_key,
            filename="serve_old.mp4",
            tech_category="serve",
            tech_tags=["发球"],
            classification_source="rule",
            confidence=1.0,
            kb_extracted=False,
            preprocessed=True,
            review_state="pending_review",
        )
        session.add(cvclf)
        await session.flush()
        # 绕开 server_default 设定 pending_since
        await session.execute(
            update(CoachVideoClassification)
            .where(CoachVideoClassification.id == cvclf_id)
            .values(pending_since=old_pending_since)
        )
        await session.commit()

    yield cvclf_id

    async with session_factory() as session:
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.id == cvclf_id
            )
        )
        await session.commit()


# ── Helpers ──────────────────────────────────────────────────────────────


def _records_with_metric(records: list[logging.LogRecord], metric: str):
    """筛出携带 ``record.metric == metric`` 的日志记录."""
    return [
        r for r in records if getattr(r, "metric", None) == metric
    ]


# ══════════════════════════════════════════════════════════════════════════
# AC2: record_pending_metrics 产生 phase=CONTENT_PREP 锚点
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_us4_ac2_pending_metrics_emits_phase_anchor(
    session_factory, pending_cvclf_with_old_pending_since, caplog
):
    """AC2: record_pending_metrics 必须产生 phase / step / pending_count 字段."""
    caplog.set_level(logging.INFO, logger="src.services.content_review.review_service")

    async with session_factory() as session:
        result = await record_pending_metrics(session)

    assert result["pending_count"] >= 1  # 至少包含本测试 fixture 的 1 行
    assert result["pending_since_p95_seconds"] is not None
    assert result["pending_since_p95_seconds"] > 0

    # 必含 metric=content_review_pending_snapshot 的 INFO 锚点
    metric_records = _records_with_metric(
        caplog.records, "content_review_pending_snapshot"
    )
    assert len(metric_records) == 1
    rec = metric_records[0]
    assert getattr(rec, "phase") == "CONTENT_PREP"
    assert getattr(rec, "step") == "content_review"
    assert isinstance(getattr(rec, "pending_count"), int)
    # p95 字段可能为 None / float；只断言存在
    assert hasattr(rec, "pending_since_p95_seconds")


# ══════════════════════════════════════════════════════════════════════════
# AC3: backlog_monitor 命中红线时产生 alert 锚点
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_us4_ac3_backlog_alert_emits_severity_anchor(
    session_factory, pending_cvclf_with_old_pending_since, caplog
):
    """AC3: 命中红线（48h > 24h 阈值）时产生 ERROR 级 + alert.severity=high 锚点."""
    # 同时捕获 review_service（pending_metrics 由 backlog_monitor 内部调用）
    caplog.set_level(logging.DEBUG, logger="src.services.content_review")

    async with session_factory() as session:
        result = await check_pending_backlog(session)

    assert result["backlog_count"] >= 1, (
        f"fixture 已 seed 一条 48h 老数据，红线 {get_settings().review_pending_red_line_hours}h；"
        f"backlog_count 必须 ≥ 1，实际 {result}"
    )

    # alert 锚点：metric + severity + phase 同时具备
    alert_records = _records_with_metric(
        caplog.records, "content_review_backlog_alert"
    )
    assert len(alert_records) == 1, (
        f"应产生 1 条 backlog_alert 日志；实际命中 {len(alert_records)} 条；"
        f"records: {[r.message for r in caplog.records[:5]]}"
    )
    alert = alert_records[0]
    assert alert.levelno >= logging.ERROR  # ERROR 级触发 SRE 高优先级告警
    assert getattr(alert, "phase") == "CONTENT_PREP"
    assert getattr(alert, "step") == "content_review"
    # severity 字段名含点（来自 logger.info(extra={"alert.severity": ...})）
    severity = getattr(alert, "alert.severity", None)
    assert severity == "high"
    # 必含命中样本与计数
    assert isinstance(getattr(alert, "backlog_count"), int)
    assert getattr(alert, "backlog_count") >= 1
    sample_ids = getattr(alert, "sample_ids", None)
    assert isinstance(sample_ids, list)
    assert str(pending_cvclf_with_old_pending_since) in sample_ids


# ══════════════════════════════════════════════════════════════════════════
# AC1: 审核决策（EP-3）日志含 phase=CONTENT_PREP + decision
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_us4_ac1_decision_log_contains_phase_anchor(
    session_factory, client, caplog
):
    """AC1: 通过 EP-3 提交决策 → 结构化日志携带 phase / step / decision 字段.

    使用 svc 直调（而非 EP-3 端点）以简化 fixture；endpoint 路径已被合约测试覆盖，
    本测试聚焦"日志锚点字段对齐"。
    """
    from src.api.schemas.content_reviews import (
        Decision,
        DecisionSubmitRequest,
    )
    from src.services.content_review.review_service import submit_decision

    # 临时 seed 一条 cvclf
    cvclf_id = uuid.uuid4()
    cos = f"charhuang/tt_video/{_TAG}/loop_001.mp4"
    async with session_factory() as session:
        session.add(
            CoachVideoClassification(
                id=cvclf_id,
                coach_name=f"{_TAG}_coach2",
                course_series=f"{_TAG}_series2",
                cos_object_key=cos,
                filename="loop_001.mp4",
                tech_category="forehand_loop_fast",
                tech_tags=[],
                classification_source="rule",
                confidence=1.0,
                kb_extracted=False,
                preprocessed=True,
            )
        )
        await session.commit()

    try:
        caplog.set_level(
            logging.INFO,
            logger="src.services.content_review.review_service",
        )
        body = DecisionSubmitRequest(
            decision=Decision.approved,
            reviewer_id="ops-ac1",
            expected_review_version=0,
        )
        async with session_factory() as session:
            await submit_decision(
                session,
                cvclf_id=cvclf_id,
                body=body,
                header_reviewer_id="ops-ac1",
            )

        # 必含 metric=content_review_decision_count 锚点
        metric_records = _records_with_metric(
            caplog.records, "content_review_decision_count"
        )
        assert len(metric_records) == 1
        rec = metric_records[0]
        assert getattr(rec, "phase") == "CONTENT_PREP"
        assert getattr(rec, "step") == "content_review"
        assert getattr(rec, "decision") == "approved"
        assert getattr(rec, "reviewer_id") == "ops-ac1"
        assert getattr(rec, "cvclf_id") == str(cvclf_id)
        # latency_seconds：fixture 没设 pending_since，应为 None（非 KeyError）
        assert hasattr(rec, "latency_seconds")
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(CoachVideoClassification).where(
                    CoachVideoClassification.id == cvclf_id
                )
            )
            await session.commit()


# ══════════════════════════════════════════════════════════════════════════
# AC4: phase 字段四阶段 Literal 范围保证（不会破坏既有日志契约）
# ══════════════════════════════════════════════════════════════════════════


def test_us4_ac4_phase_literal_covers_four_phases():
    """AC4: ``_PHASE_BY_TASK_TYPE`` 映射的取值必须仅在四阶段 Literal 内.

    防止后续重构误把某个 task_type 改派到无效阶段（如 'WTF'），
    导致 SRE 仪表盘聚合时出现脏值。
    """
    from src.services.task_submission_service import _PHASE_BY_TASK_TYPE

    valid_phases = {"CONTENT_PREP", "TRAINING", "STANDARDIZATION", "INFERENCE"}
    for task_type, phase in _PHASE_BY_TASK_TYPE.items():
        assert phase in valid_phases, (
            f"task_type={task_type} 派生到非法 phase={phase}; "
            f"合法集合={sorted(valid_phases)}"
        )

    # CONTENT_PREP 至少包含 video_classification + video_preprocessing + video_curation
    content_prep_task_types = [
        tt.value for tt, ph in _PHASE_BY_TASK_TYPE.items()
        if ph == "CONTENT_PREP"
    ]
    assert "video_classification" in content_prep_task_types
    assert "video_preprocessing" in content_prep_task_types
    assert "video_curation" in content_prep_task_types

    # TRAINING 仅保留 kb_extraction
    training_task_types = [
        tt.value for tt, ph in _PHASE_BY_TASK_TYPE.items()
        if ph == "TRAINING"
    ]
    assert training_task_types == ["kb_extraction"], (
        f"TRAINING 阶段映射变化：当前 {training_task_types}, 期望仅 ['kb_extraction']"
    )
