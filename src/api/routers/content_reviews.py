"""Feature-022 内容审核工作台路由 (US2/US3 MVP).

5 个 endpoint：
  EP-1  GET    /api/v1/content-reviews             列出审核条目
  EP-2  GET    /api/v1/content-reviews/{cvclf_id}  详情（含清洗摘要 + 决策历史）
  EP-3  POST   /api/v1/content-reviews/{cvclf_id}/decisions  提交决策（乐观锁）
  EP-4  GET    /api/v1/content-reviews/stats       时间窗审核统计
  EP-5a GET    /api/v1/admin/review-gate           查询审核门开关
  EP-5b PATCH  /api/v1/admin/review-gate           切换审核门开关

严格对齐 ``specs/022-content-review-workflow/contracts/content-reviews.yaml``。
所有响应必须走章程 v2.0.0 的统一信封；所有错误统一抛 :class:`AppException`。

T023 重构：路由层只做参数校验 + 响应组装；业务逻辑（含状态机转移、
乐观锁、统计聚合）全部下沉到 :mod:`src.services.content_review.review_service`，
严格遵守章程「分层架构」原则。EP-5a/EP-5b 的开关切换因实现简单（仅 1-2 行 raw SQL）
保留在路由层。
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.content_reviews import (
    ContentReviewDetail,
    ContentReviewItem,
    DecisionSubmitRequest,
    ReviewDecision,
    ReviewGateConfig,
    ReviewGatePatchRequest,
    StatsResponse,
)
from src.api.schemas.envelope import SuccessEnvelope, ok, page
from src.db.session import get_db
from src.services.content_review.review_service import (
    ListReviewsFilters,
    get_review_detail as svc_get_review_detail,
    get_stats as svc_get_stats,
    list_reviews as svc_list_reviews,
    submit_decision as svc_submit_decision,
)
from src.utils.time_utils import now_cst


logger = logging.getLogger(__name__)


# 注意：本文件同时承载 /content-reviews 与 /admin/review-gate 两组路径前缀，
# 因 FastAPI APIRouter 的 prefix 是按"该 router 全部路径共享前缀"，所以这里
# 不指定 prefix，由各 endpoint 自己声明绝对资源段。main.py 仍按 /api/v1 拼接。
router = APIRouter(tags=["content-reviews"])


# ══════════════════════════════════════════════════════════════════════════
# EP-1: GET /content-reviews
# ══════════════════════════════════════════════════════════════════════════


@router.get(
    "/content-reviews",
    response_model=SuccessEnvelope[list[ContentReviewItem]],
    summary="EP-1: 列出审核条目",
)
async def list_content_reviews(
    state: str | None = Query(
        None,
        description=(
            "审核状态过滤；省略时默认查询 pending_review + approved + stale 三态"
            "（不含 rejected，澄清 Q5）"
        ),
    ),
    coach_name: str | None = Query(None),
    tech_category: str | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    page_num: int = Query(1, alias="page", ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[ContentReviewItem]]:
    result = await svc_list_reviews(
        db,
        filters=ListReviewsFilters(
            state=state,
            coach_name=coach_name,
            tech_category=tech_category,
            from_=from_,
            to=to,
        ),
        page=page_num,
        page_size=page_size,
    )
    return page(
        result.items, page=page_num, page_size=page_size, total=result.total
    )


# ══════════════════════════════════════════════════════════════════════════
# EP-4: GET /content-reviews/stats（必须在 /content-reviews/{cvclf_id} 之前注册，
# 否则 /stats 会被当成 cvclf_id 路由匹配）
# ══════════════════════════════════════════════════════════════════════════


@router.get(
    "/content-reviews/stats",
    response_model=SuccessEnvelope[StatsResponse],
    summary="EP-4: 时间窗审核统计",
)
async def get_review_stats(
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    group_by: str | None = Query(None, pattern="^(reviewer|reason|day)$"),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[StatsResponse]:
    stats = await svc_get_stats(db, from_=from_, to=to, group_by=group_by)
    return ok(stats)


# ══════════════════════════════════════════════════════════════════════════
# EP-2: GET /content-reviews/{cvclf_id}
# ══════════════════════════════════════════════════════════════════════════


@router.get(
    "/content-reviews/{cvclf_id}",
    response_model=SuccessEnvelope[ContentReviewDetail],
    summary="EP-2: 单条审核条目详情（含清洗摘要 + 决策历史）",
)
async def get_content_review_detail(
    cvclf_id: UUID = Path(...),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[ContentReviewDetail]:
    detail = await svc_get_review_detail(db, cvclf_id=cvclf_id)
    return ok(detail)


# ══════════════════════════════════════════════════════════════════════════
# EP-3: POST /content-reviews/{cvclf_id}/decisions
# ══════════════════════════════════════════════════════════════════════════


@router.post(
    "/content-reviews/{cvclf_id}/decisions",
    response_model=SuccessEnvelope[ReviewDecision],
    status_code=200,
    summary="EP-3: 提交审核决策",
)
async def submit_review_decision(
    body: DecisionSubmitRequest,
    cvclf_id: UUID = Path(...),
    x_reviewer_id: str = Header(..., alias="X-Reviewer-Id", max_length=64),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[ReviewDecision]:
    decision = await svc_submit_decision(
        db,
        cvclf_id=cvclf_id,
        body=body,
        header_reviewer_id=x_reviewer_id,
    )
    return ok(decision)


# ══════════════════════════════════════════════════════════════════════════
# EP-5a / EP-5b: GET / PATCH /admin/review-gate
# ══════════════════════════════════════════════════════════════════════════


# 因 admin/review-gate 路径与 content-reviews 同 router 共存（plan.md § 4），
# 这里的 endpoint 直接用绝对路径声明。
# 审核门状态保存在 ``task_channel_configs.content_review_gate``：
#   - enabled         审核门开关（true=严格 / false=绕过）
#   - updated_at      最近一次切换时间（last_toggled_at）
#   - 审核员标识与原因不入 DB（task_channel_configs schema 限制），
#     只走结构化日志留痕（运维事后可由日志反查；后续若有合规需求再单独加表）


@router.get(
    "/admin/review-gate",
    response_model=SuccessEnvelope[ReviewGateConfig],
    summary="EP-5a: 查询审核门开关状态",
)
async def get_review_gate(
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[ReviewGateConfig]:
    # raw SQL：避开 TaskChannelConfig.task_type 的 Enum(TaskType) 约束
    # （content_review_gate 不在 TaskType 枚举里，它仅作为配置承载体）
    row = (
        await db.execute(
            text(
                "SELECT enabled, updated_at FROM task_channel_configs "
                "WHERE task_type = 'content_review_gate'"
            )
        )
    ).first()
    if row is None:
        # 配置行缺失（迁移未跑或被误删）—— 按 fail-secure 默认严格门
        return ok(
            ReviewGateConfig(
                enabled=True,
                last_toggled_at=None,
                last_toggled_by=None,
            )
        )

    return ok(
        ReviewGateConfig(
            enabled=bool(row[0]),
            last_toggled_at=row[1],
            last_toggled_by=None,  # task_channel_configs 没有 operator 列
        )
    )


@router.patch(
    "/admin/review-gate",
    response_model=SuccessEnvelope[ReviewGateConfig],
    summary="EP-5b: 切换审核门开关",
)
async def patch_review_gate(
    body: ReviewGatePatchRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[ReviewGateConfig]:
    # 应用层补充校验（除 Pydantic 字段约束外）：reason 不能仅空格
    if not body.reason.strip():
        raise AppException(
            ErrorCode.REVIEW_GATE_INVALID_STATE,
            details={"field": "reason", "value": body.reason},
        )

    now = now_cst()
    # raw SQL update + RETURNING：避开 TaskChannelConfig.task_type 的 Enum 约束
    result = await db.execute(
        text(
            "UPDATE task_channel_configs "
            "SET enabled = :enabled, updated_at = :now "
            "WHERE task_type = 'content_review_gate' "
            "RETURNING enabled, updated_at"
        ),
        {"enabled": body.enabled, "now": now},
    )
    row = result.first()
    if row is None:
        # 配置行缺失 → 报错让运维注意（迁移未跑）；不静默 INSERT 兜底
        raise AppException(
            ErrorCode.INTERNAL_ERROR,
            message=(
                "task_channel_configs.content_review_gate row missing; "
                "did migration 0021 run?"
            ),
        )

    await db.commit()

    # 审计日志：含 operator_id + reason（不入 DB；只走结构化日志）
    logger.warning(
        "review_gate.toggle: enabled=%s operator_id=%s reason=%r "
        "previous_enabled=%s",
        body.enabled, body.operator_id, body.reason,
        not body.enabled,  # 仅作直观说明（精确值需查日志/审计平台）
    )

    return ok(
        ReviewGateConfig(
            enabled=bool(row[0]),
            last_toggled_at=row[1],
            last_toggled_by=body.operator_id,
        )
    )


__all__ = ["router"]
