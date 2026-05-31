"""Feature-022 内容审核工作台 — 业务服务层 (T023).

将 [content_reviews.py](../api/routers/content_reviews.py) 路由层的业务逻辑下沉到此处，
严格遵守章程「分层架构」：路由只做参数校验+响应组装，业务逻辑一律下沉到 services/。

提供 4 个核心方法：
  - :func:`list_reviews`        EP-1：分页列表 + 过滤 + 排序（research.md R7 SQL 形态）
  - :func:`get_review_detail`   EP-2：详情（含清洗摘要 + 决策历史）
  - :func:`submit_decision`     EP-3：决策提交（乐观锁 + 状态机 + 同事务一致性）
  - :func:`get_stats`           EP-4：时间窗审核统计聚合

所有方法均接受 ``AsyncSession`` 参数，**不自行管理事务**（commit/rollback 由调用方控制），
唯一例外是 :func:`submit_decision` 因状态机转换需要同事务原子性，内部完成 commit。

错误处理统一抛 :class:`AppException`，由路由层异常处理器转为信封；
内部不应直接抛 :class:`HTTPException`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.content_reviews import (
    ContentReviewDetail,
    ContentReviewItem,
    CurationSegmentSample,
    CurationSummary,
    Decision,
    DecisionSubmitRequest,
    ReasonBreakdown,
    ReasonCode,
    ReviewDecision,
    ReviewState,
    ReviewerThroughput,
    StatsResponse,
)
from src.models.coach_video_classification import CoachVideoClassification
from src.models.content_review_decision import ContentReviewDecision
from src.models.video_curation_job import VideoCurationJob
from src.models.video_curation_segment_result import VideoCurationSegmentResult
from src.utils.time_utils import now_cst


logger = logging.getLogger(__name__)


_VALID_REVIEW_STATES = {"pending_review", "approved", "rejected", "stale"}


# ══════════════════════════════════════════════════════════════════════════
# Schema 映射 helpers
# ══════════════════════════════════════════════════════════════════════════


def _to_review_decision(row: ContentReviewDecision) -> ReviewDecision:
    """ORM → ReviewDecision 响应模型."""
    return ReviewDecision(
        id=row.id,
        decision=Decision(row.decision),
        reason_code=ReasonCode(row.reason_code) if row.reason_code else None,
        note=row.note,
        reviewer_id=row.reviewer_id,
        decided_at=row.decided_at,
        cleansing_version=row.cleansing_version,
        superseded_at=row.superseded_at,
    )


def _to_review_item(
    cvclf: CoachVideoClassification,
    last_decision: ContentReviewDecision | None,
) -> ContentReviewItem:
    """ORM → ContentReviewItem 响应模型."""
    return ContentReviewItem(
        id=cvclf.id,
        coach_name=cvclf.coach_name,
        tech_category=cvclf.action or "unclassified",  # Feature-023: ORM 字段 tech_category → action; schema 兼容字段名保留
        cos_object_key=cvclf.cos_object_key,
        filename=cvclf.filename,
        review_state=ReviewState(cvclf.review_state),
        review_version=int(cvclf.review_version),
        pending_since=cvclf.pending_since,
        cleansing_version=cvclf.last_curation_job_id,
        last_decision=(
            _to_review_decision(last_decision) if last_decision else None
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
# EP-1: list_reviews
# ══════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class ListReviewsFilters:
    """list_reviews 的过滤条件（路由层组装后传入）.

    Attributes:
        state: 审核状态过滤；None 表示默认（过滤 rejected，澄清 Q5）
        coach_name: 教练名等值过滤
        tech_category: 技术类别等值过滤
        from_: 创建时间下界（ISO 时间）
        to: 创建时间上界（ISO 时间）
    """

    state: str | None = None
    coach_name: str | None = None
    tech_category: str | None = None
    from_: datetime | None = None
    to: datetime | None = None


@dataclass(slots=True)
class ListReviewsResult:
    """list_reviews 的返回值（路由层用 ``page()`` 包装为信封）."""

    items: list[ContentReviewItem]
    total: int


async def list_reviews(
    session: AsyncSession,
    *,
    filters: ListReviewsFilters,
    page: int = 1,
    page_size: int = 20,
) -> ListReviewsResult:
    """EP-1: 分页列出审核条目（research.md R7 SQL 形态）.

    SQL 形态约束：
    - 默认列表过滤 ``rejected``（澄清 Q5 永久保留，列表中默认隐藏）；除非显式 ``?state=rejected``
    - 排序：``ORDER BY pending_since ASC NULLS LAST, created_at DESC``
        - 优先级：积压最久的优先（pending_since 升序）
        - 兜底：created_at DESC（同时刻批量入队时保持稳定排序）
    - 索引利用：(state, pending_since) 复合索引（迁移 0021 内建）

    Args:
        session: 调用方提供的 AsyncSession
        filters: 过滤条件（见 :class:`ListReviewsFilters`）
        page: 页码（从 1 起）
        page_size: 每页数量（1-100）

    Raises:
        AppException(INVALID_ENUM_VALUE): ``state`` 非法值
    """
    # 参数校验：state 非法值 → 400 INVALID_ENUM_VALUE
    if filters.state is not None and filters.state not in _VALID_REVIEW_STATES:
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            details={
                "field": "state",
                "value": filters.state,
                "allowed": sorted(_VALID_REVIEW_STATES),
            },
        )

    # 构造 where 条件
    conds = []
    if filters.state is None:
        # 默认过滤掉 rejected（澄清 Q5）
        conds.append(CoachVideoClassification.review_state != "rejected")
    else:
        conds.append(CoachVideoClassification.review_state == filters.state)
    if filters.coach_name:
        conds.append(CoachVideoClassification.coach_name == filters.coach_name)
    if filters.tech_category:
        conds.append(
            CoachVideoClassification.action == filters.tech_category
        )
    if filters.from_:
        conds.append(CoachVideoClassification.created_at >= filters.from_)
    if filters.to:
        conds.append(CoachVideoClassification.created_at <= filters.to)

    # 总数（先 COUNT 再分页查 — 与现有接口一致）
    count_stmt = select(func.count()).select_from(CoachVideoClassification)
    if conds:
        count_stmt = count_stmt.where(and_(*conds))
    total = (await session.execute(count_stmt)).scalar_one()

    # 分页数据：左联 ContentReviewDecision 取 last_decision
    stmt = (
        select(CoachVideoClassification, ContentReviewDecision)
        .join(
            ContentReviewDecision,
            CoachVideoClassification.last_decision_id
            == ContentReviewDecision.id,
            isouter=True,
        )
        .order_by(
            CoachVideoClassification.pending_since.asc().nulls_last(),
            CoachVideoClassification.created_at.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if conds:
        stmt = stmt.where(and_(*conds))

    rows = (await session.execute(stmt)).all()
    items = [_to_review_item(cvclf, last_dec) for cvclf, last_dec in rows]

    return ListReviewsResult(items=items, total=int(total))


# ══════════════════════════════════════════════════════════════════════════
# EP-2: get_review_detail
# ══════════════════════════════════════════════════════════════════════════


async def get_review_detail(
    session: AsyncSession,
    *,
    cvclf_id: UUID,
) -> ContentReviewDetail:
    """EP-2: 单条审核条目详情（含清洗摘要 + 决策历史）.

    Raises:
        AppException(NOT_FOUND): cvclf_id 不存在
    """
    cvclf = (
        await session.execute(
            select(CoachVideoClassification).where(
                CoachVideoClassification.id == cvclf_id
            )
        )
    ).scalar_one_or_none()
    if cvclf is None:
        raise AppException(
            ErrorCode.NOT_FOUND,
            details={"resource": "content_review", "id": str(cvclf_id)},
        )

    # last_decision
    last_decision: ContentReviewDecision | None = None
    if cvclf.last_decision_id is not None:
        last_decision = (
            await session.execute(
                select(ContentReviewDecision).where(
                    ContentReviewDecision.id == cvclf.last_decision_id
                )
            )
        ).scalar_one_or_none()

    # decision_history（按 decided_at desc）
    history_rows = (
        (
            await session.execute(
                select(ContentReviewDecision)
                .where(ContentReviewDecision.cvclf_id == cvclf_id)
                .order_by(ContentReviewDecision.decided_at.desc())
            )
        )
        .scalars()
        .all()
    )

    # curation_summary（取最近一次 success 清洗作业，含若干样例 segment）
    curation_summary: CurationSummary | None = None
    if cvclf.last_curation_job_id is not None:
        cur_job = (
            await session.execute(
                select(VideoCurationJob).where(
                    VideoCurationJob.id == cvclf.last_curation_job_id
                )
            )
        ).scalar_one_or_none()
        if cur_job is not None and cur_job.status == "success":
            sample_rows = (
                (
                    await session.execute(
                        select(VideoCurationSegmentResult)
                        .where(
                            VideoCurationSegmentResult.job_id == cur_job.id,
                            VideoCurationSegmentResult.effective_decision
                            == "accepted",
                        )
                        .order_by(
                            VideoCurationSegmentResult.segment_index.asc()
                        )
                        .limit(5)
                    )
                )
                .scalars()
                .all()
            )
            samples = [
                CurationSegmentSample(
                    start_seconds=row.segment_start_ms / 1000.0,
                    end_seconds=row.segment_end_ms / 1000.0,
                    transcript_excerpt="",  # 暂不联表 transcript；后续可拓展
                )
                for row in sample_rows
            ]
            curation_summary = CurationSummary(
                total_segments=int(cur_job.total_segment_count or 0),
                accepted_segments=int(cur_job.accepted_segment_count or 0),
                rejected_segments=int(cur_job.rejected_segment_count or 0),
                accepted_duration_ratio=float(
                    cur_job.accepted_duration_ratio or 0.0
                ),
                sample_segments=samples,
            )

    base = _to_review_item(cvclf, last_decision)
    return ContentReviewDetail(
        **base.model_dump(),
        curation_summary=curation_summary,
        decision_history=[_to_review_decision(r) for r in history_rows],
    )


# ══════════════════════════════════════════════════════════════════════════
# EP-3: submit_decision (乐观锁 + 状态机)
# ══════════════════════════════════════════════════════════════════════════


async def submit_decision(
    session: AsyncSession,
    *,
    cvclf_id: UUID,
    body: DecisionSubmitRequest,
    header_reviewer_id: str,
) -> ReviewDecision:
    """EP-3: 提交审核决策（含乐观锁 + 状态机 + 同事务原子性）.

    业务规则：
    1. ``X-Reviewer-Id`` header 与 body.reviewer_id 必须一致（防身份伪造）
    2. ``decision=rejected`` 必须带 ``reason_code``
    3. ``reason_code=other`` 必须带 ``note``（应用层补充校验）
    4. 主表行用 ``with_for_update()`` 加锁，防止并发提交
    5. 状态机：仅 ``review_state=pending_review`` 可接受决策
    6. 乐观锁：``expected_review_version`` 与 DB 当前值不一致 → 409
    7. 同事务原子完成：旧决策行 superseded_at 标记 + 新决策行 INSERT +
       主表三字段更新（review_state / last_decision_id / review_version+1 / pending_since=NULL）

    本方法**自行 commit**（业务事务必须原子可见）；调用方无需再 commit。

    Raises:
        AppException(INVALID_REVIEWER_IDENTITY): header vs body 不一致
        AppException(REJECTED_REQUIRES_REASON): rejected 缺 reason_code
        AppException(VALIDATION_FAILED): reason_code=other 缺 note
        AppException(NOT_FOUND): cvclf_id 不存在
        AppException(REVIEW_NOT_PENDING): 状态机不允许（非 pending_review）
        AppException(REVIEW_VERSION_CONFLICT): 乐观锁版本号不一致
    """
    # ── 1. header / body reviewer_id 一致性 ───────────────────────────
    if header_reviewer_id != body.reviewer_id:
        raise AppException(
            ErrorCode.INVALID_REVIEWER_IDENTITY,
            details={
                "header_value": header_reviewer_id,
                "body_value": body.reviewer_id,
            },
        )

    # ── 2. decision=rejected 必填 reason_code ─────────────────────────
    if body.decision == Decision.rejected and body.reason_code is None:
        raise AppException(
            ErrorCode.REJECTED_REQUIRES_REASON,
            details={"decision": body.decision.value, "reason_code": None},
        )
    # reason_code=other 必须配 note（data-model.md § 4.2）
    if (
        body.decision == Decision.rejected
        and body.reason_code == ReasonCode.other
        and not body.note
    ):
        raise AppException(
            ErrorCode.VALIDATION_FAILED,
            message="reason_code=other 时必须提供 note 字段说明原因",
            details={"field": "note", "value": None},
        )

    # ── 3. 加锁取 cvclf 行 + 校验状态机 + 乐观锁 ──────────────────────
    cvclf = (
        await session.execute(
            select(CoachVideoClassification)
            .where(CoachVideoClassification.id == cvclf_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if cvclf is None:
        raise AppException(
            ErrorCode.NOT_FOUND,
            details={"resource": "content_review", "id": str(cvclf_id)},
        )

    # 状态机：必须 pending_review（stale / approved / rejected 都不允许直接提交）
    if cvclf.review_state != "pending_review":
        raise AppException(
            ErrorCode.REVIEW_NOT_PENDING,
            details={
                "cvclf_id": str(cvclf_id),
                "current_review_state": cvclf.review_state,
                "current_review_version": int(cvclf.review_version),
            },
        )

    # 乐观锁
    if int(cvclf.review_version) != body.expected_review_version:
        raise AppException(
            ErrorCode.REVIEW_VERSION_CONFLICT,
            details={
                "cvclf_id": str(cvclf_id),
                "expected_version": body.expected_review_version,
                "current_version": int(cvclf.review_version),
            },
        )

    # ── 4. 写入 content_review_decisions + 更新主表（同事务） ────────
    now = now_cst()

    # 4.1 把当前指向旧决策（若有）的 superseded_at 标记为 now
    if cvclf.last_decision_id is not None:
        await session.execute(
            update(ContentReviewDecision)
            .where(
                ContentReviewDecision.id == cvclf.last_decision_id,
                ContentReviewDecision.superseded_at.is_(None),
            )
            .values(superseded_at=now)
        )

    # 4.2 INSERT 新决策行
    new_decision = ContentReviewDecision(
        cvclf_id=cvclf_id,
        cleansing_version=cvclf.last_curation_job_id,
        decision=body.decision.value,
        reason_code=body.reason_code.value if body.reason_code else None,
        note=body.note,
        reviewer_id=body.reviewer_id,
        decided_at=now,
        superseded_at=None,
    )
    session.add(new_decision)
    await session.flush()  # 拿到 new_decision.id

    # 4.3 更新主表：review_state / last_decision_id / review_version+1 / pending_since=NULL
    new_review_state = (
        "approved" if body.decision == Decision.approved else "rejected"
    )
    old_review_version = int(cvclf.review_version)
    new_review_version = old_review_version + 1
    await session.execute(
        update(CoachVideoClassification)
        .where(CoachVideoClassification.id == cvclf_id)
        .values(
            review_state=new_review_state,
            last_decision_id=new_decision.id,
            review_version=new_review_version,
            pending_since=None,
        )
    )
    await session.commit()

    # ── 5. 结构化日志 + 指标埋点（T030 / FR-012） ──────────────────────
    # decided_at - pending_since 即"审核延迟"指标（从入队到决策落地）
    pending_since = cvclf.pending_since
    latency_seconds: float | None = None
    if pending_since is not None:
        latency_seconds = (now - pending_since).total_seconds()

    logger.info(
        "review_decision.submit: cvclf_id=%s reviewer_id=%s decision=%s "
        "reason_code=%s review_version=%d→%d latency=%s",
        cvclf_id,
        body.reviewer_id,
        body.decision.value,
        body.reason_code.value if body.reason_code else None,
        old_review_version,
        new_review_version,
        f"{latency_seconds:.1f}s" if latency_seconds is not None else "n/a",
        extra={
            # 章程原则 V「可观测性」+ Feature-018 § 7.2 步骤级监控锚点
            # 指标 1: content_review_decision_count{decision=...}
            "metric": "content_review_decision_count",
            "phase": "CONTENT_PREP",
            "step": "content_review",
            "decision": body.decision.value,
            "reason_code": body.reason_code.value if body.reason_code else None,
            "reviewer_id": body.reviewer_id,
            "cvclf_id": str(cvclf_id),
            # 指标 3: content_review_latency_seconds（仅当 pending_since 非空时埋）
            "latency_seconds": latency_seconds,
        },
    )

    # ── 6. 重新取一次新决策（含 server_default 的 decided_at 等） ─────
    refreshed = (
        await session.execute(
            select(ContentReviewDecision).where(
                ContentReviewDecision.id == new_decision.id
            )
        )
    ).scalar_one()
    return _to_review_decision(refreshed)


# ══════════════════════════════════════════════════════════════════════════
# EP-4: get_stats
# ══════════════════════════════════════════════════════════════════════════


async def get_stats(
    session: AsyncSession,
    *,
    from_: datetime,
    to: datetime,
    group_by: str | None = None,  # noqa: ARG001  T023 占位参数（保留接口形态）
) -> StatsResponse:
    """EP-4: 时间窗审核统计聚合.

    返回 4 个聚合维度：
    - 总览：total / approved / rejected / approval_rate
    - 平均时延：avg_latency_seconds（pending_since → decided_at 的均值）
    - per_reviewer：审核员产出量降序
    - per_reason：拒绝原因分布降序

    ``group_by`` 当前作为占位参数（T023 不区分），后续可在此分支扩展按 day/week 维度展开。
    """
    # 章程 v2.0 时区规范：全项目使用 CST 北京时间裸时间（tz-naive）。
    # FastAPI 自动解析 ISO-8601 query 参数时若客户端带 tz 偏移（如 +08:00），
    # 会得到 tz-aware datetime，与 DB 列 TIMESTAMP(timezone=False) 比较时
    # asyncpg 会抛 "can't subtract offset-naive and offset-aware datetimes"。
    # 此处统一剥时区：客户端如传 +08:00（CST 同语义）直接当裸时间用；
    # 若传 UTC 时间（+00:00）则视为客户端违反章程，仍按裸时间处理（语义自负）。
    if from_.tzinfo is not None:
        from_ = from_.replace(tzinfo=None)
    if to.tzinfo is not None:
        to = to.replace(tzinfo=None)

    # ── 总数与按 decision 聚合 ─────────────────────────────────────────
    total_stmt = (
        select(
            ContentReviewDecision.decision,
            func.count().label("cnt"),
        )
        .where(
            ContentReviewDecision.decided_at >= from_,
            ContentReviewDecision.decided_at <= to,
        )
        .group_by(ContentReviewDecision.decision)
    )
    total_rows = (await session.execute(total_stmt)).all()
    counts = {row[0]: int(row[1]) for row in total_rows}
    approved = counts.get("approved", 0)
    rejected = counts.get("rejected", 0)
    total = approved + rejected
    approval_rate = (approved / total) if total > 0 else 0.0

    # ── 平均时延（pending_since → decided_at 的 epoch 差） ─────────────
    latency_stmt = (
        select(
            func.avg(
                func.extract(
                    "epoch",
                    ContentReviewDecision.decided_at
                    - CoachVideoClassification.pending_since,
                )
            )
        )
        .select_from(ContentReviewDecision)
        .join(
            CoachVideoClassification,
            CoachVideoClassification.id == ContentReviewDecision.cvclf_id,
        )
        .where(
            ContentReviewDecision.decided_at >= from_,
            ContentReviewDecision.decided_at <= to,
            CoachVideoClassification.pending_since.is_not(None),
        )
    )
    avg_latency = (await session.execute(latency_stmt)).scalar_one_or_none()

    # ── per_reviewer ──────────────────────────────────────────────────
    reviewer_stmt = (
        select(
            ContentReviewDecision.reviewer_id,
            func.count().label("cnt"),
        )
        .where(
            ContentReviewDecision.decided_at >= from_,
            ContentReviewDecision.decided_at <= to,
        )
        .group_by(ContentReviewDecision.reviewer_id)
        .order_by(func.count().desc())
    )
    reviewer_rows = (await session.execute(reviewer_stmt)).all()
    per_reviewer = [
        ReviewerThroughput(reviewer_id=r[0], decisions=int(r[1]))
        for r in reviewer_rows
    ]

    # ── per_reason ────────────────────────────────────────────────────
    reason_stmt = (
        select(
            ContentReviewDecision.reason_code,
            func.count().label("cnt"),
        )
        .where(
            ContentReviewDecision.decided_at >= from_,
            ContentReviewDecision.decided_at <= to,
            ContentReviewDecision.decision == "rejected",
            ContentReviewDecision.reason_code.is_not(None),
        )
        .group_by(ContentReviewDecision.reason_code)
        .order_by(func.count().desc())
    )
    reason_rows = (await session.execute(reason_stmt)).all()
    per_reason = [
        ReasonBreakdown(reason_code=ReasonCode(r[0]), count=int(r[1]))
        for r in reason_rows
    ]

    return StatsResponse(
        **{
            "from": from_,
            "to": to,
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "approval_rate": approval_rate,
            "avg_latency_seconds": (
                float(avg_latency) if avg_latency is not None else None
            ),
            "per_reviewer": per_reviewer,
            "per_reason": per_reason,
        }
    )


__all__ = [
    "ListReviewsFilters",
    "ListReviewsResult",
    "list_reviews",
    "get_review_detail",
    "submit_decision",
    "get_stats",
    "record_pending_metrics",
]


# ══════════════════════════════════════════════════════════════════════════
# T030 · 指标埋点 — pending count / pending_since p95（周期采样）
# ══════════════════════════════════════════════════════════════════════════


async def record_pending_metrics(session: AsyncSession) -> dict[str, float]:
    """周期采样 ``coach_video_classifications`` 中 pending_review 行的两个指标:

      - 指标 2: ``content_review_pending_count`` 当前积压总数
      - 指标 4: ``content_review_pending_since_p95_seconds`` 等待时长 p95

    采样结果同时通过结构化日志埋点（``metric=...``）暴露给 SRE 仪表盘聚合;
    返回值则便于 backlog_monitor 复用做阈值判定（不做重复 SQL）。

    采样口径（review_state='pending_review'）刻意只覆盖"等待人工决策"路径,
    不含 stale（清洗变更后失效）/ approved / rejected。

    Returns:
        ``{"pending_count": int, "pending_since_p95_seconds": float | None}``
    """
    # 一次扫描拿 count + p95，避免双 SQL
    stmt = select(
        func.count().label("cnt"),
        func.percentile_cont(0.95).within_group(
            func.extract(
                "epoch",
                func.now() - CoachVideoClassification.pending_since,
            )
        ).label("p95"),
    ).where(
        CoachVideoClassification.review_state == "pending_review",
        CoachVideoClassification.pending_since.is_not(None),
    )
    row = (await session.execute(stmt)).one()
    pending_count = int(row.cnt or 0)
    p95_seconds = float(row.p95) if row.p95 is not None else None

    logger.info(
        "metric: content_review_pending_count=%d "
        "content_review_pending_since_p95_seconds=%s",
        pending_count,
        f"{p95_seconds:.1f}" if p95_seconds is not None else "n/a",
        extra={
            "metric": "content_review_pending_snapshot",
            "phase": "CONTENT_PREP",
            "step": "content_review",
            "pending_count": pending_count,
            "pending_since_p95_seconds": p95_seconds,
        },
    )

    return {
        "pending_count": pending_count,
        "pending_since_p95_seconds": p95_seconds,
    }
