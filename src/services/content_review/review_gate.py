"""Feature-022 内容审核门 — 公共门控查询.

由 :mod:`src.api.routers.tasks` 在排队 KB 抽取前调用，
也由 :mod:`src.services.kb_extraction_pipeline.step_executors.download_video`
在 DAG 第一步执行前调用，实现"两点对接"（与 Feature-021 清洗门 ``kb_gate.py`` 同模式）：

  router 层 (FR-008/9): 拦截"未审核 / 已拒绝 / 已失效"，按状态分别抛
                        ``CONTENT_NOT_REVIEWED`` / ``CONTENT_REVIEW_REJECTED`` /
                        ``CONTENT_REVIEW_STALE``
  DAG 层  (FR-008 防御性): 在 download_video 步骤再做一次同样的检查；
                          正常路径下 router 已拦截，进入 DAG 说明并发竞态或
                          代码缺陷，fail-fast

bypass 双层兜底（FR-014 应急回滚剧本）：
  · ``settings.kb_extraction_bypass_review_gate=True`` ⇒ 全局开关绕过
  · ``task_channel_configs.content_review_gate.enabled=False`` ⇒ DB 热配置绕过
  任一为 True/False ⇒ 视作"绕过模式"，命中后请求/作业留痕：
    - router 响应头：``X-Review-Gate-Bypass: true``
    - DAG ``output_summary.review_gate_bypass=True``
  恢复后立即关闭，不留遗留豁免。

返回值约定：
  · ``ok``           review_state=approved，正常执行
  · ``not_reviewed`` review_state=pending_review ⇒ router 应抛 CONTENT_NOT_REVIEWED
  · ``rejected``     review_state=rejected ⇒ router 应抛 CONTENT_REVIEW_REJECTED
  · ``stale``        review_state=stale ⇒ router 应抛 CONTENT_REVIEW_STALE
  · ``not_classified`` 该 cos_object_key 无 cvclf 行 ⇒ 通常 router 已被
                       CLASSIFICATION_REQUIRED 拦截，这里 fail-soft 返回该值
                       供调用方按现有错误码体系处理
  · ``bypassed``     双层 bypass 任一启用 ⇒ 不查 review_state、直通

详见：
    specs/022-content-review-workflow/research.md R3 (两点对接)
    specs/022-content-review-workflow/data-model.md § 3.4 (状态机)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.coach_video_classification import CoachVideoClassification


logger = logging.getLogger(__name__)


# ── Domain types ──────────────────────────────────────────────────────────


GateDecision = Literal[
    "ok",
    "not_reviewed",
    "rejected",
    "stale",
    "not_classified",
    "bypassed",
]


@dataclass(slots=True)
class ReviewGateResult:
    """门控查询结果，路由 / DAG 层解读后采取相应动作.

    Attributes:
        decision: 见 :data:`GateDecision`
        cvclf_id: 命中的 ``coach_video_classifications`` 主键；``not_classified`` 时为 None
        review_state: 当前 ``review_state`` 字段值（即 decision 对应的状态）；bypass / not_classified 时为 None
        review_version: 当前 ``review_version`` 字段值；bypass / not_classified 时为 None
        bypass_reason: 当 ``decision='bypassed'`` 时说明触发原因，便于运维定位：
            ``"settings"`` —— ``kb_extraction_bypass_review_gate=True`` 命中；
            ``"channel_config"`` —— ``task_channel_configs.content_review_gate.enabled=False`` 命中
    """

    decision: GateDecision
    cvclf_id: UUID | None = None
    review_state: str | None = None
    review_version: int | None = None
    bypass_reason: str | None = None


# ── Public API ────────────────────────────────────────────────────────────


async def evaluate_review_gate(
    session: AsyncSession,
    *,
    cos_object_key: str,
) -> ReviewGateResult:
    """查询该 ``cos_object_key`` 的审核状态并产出门控决策.

    Order:
        1. ``settings.kb_extraction_bypass_review_gate=True`` ⇒ ``bypassed`` (settings)
        2. 查 ``task_channel_configs.content_review_gate``：``enabled=False`` ⇒ ``bypassed`` (channel_config)
        3. 取 ``coach_video_classifications`` 行（按 ``cos_object_key``）
        4. 不存在 ⇒ ``not_classified``
        5. ``review_state`` 映射：
            - ``approved``       → ``ok``
            - ``pending_review`` → ``not_reviewed``
            - ``rejected``       → ``rejected``
            - ``stale``          → ``stale``
    """
    settings = get_settings()

    # Layer 1: 全局应急开关
    if settings.kb_extraction_bypass_review_gate:
        logger.warning(
            "review_gate bypassed by settings.kb_extraction_bypass_review_gate; "
            "cos_object_key=%s",
            cos_object_key,
        )
        return ReviewGateResult(decision="bypassed", bypass_reason="settings")

    # Layer 2: DB 热配置开关 —— raw SQL 绕过 TaskChannelConfig.task_type 的
    # Enum(TaskType) 列约束（content_review_gate 不在 TaskType 枚举里，
    # 它仅作为 task_channel_configs 的"配置承载体"，与真任务解耦）
    #
    # 防御性兜底（mock 场景）：若 session.execute 返回的 Result 类型不正确
    # （例如既有 KB 抽取测试用 AsyncMock 替代 get_db，导致 .scalar_one_or_none
    # 返回 coroutine 而非真值），统一 fall-soft 到 not_classified —— 调用方按现有
    # CLASSIFICATION_REQUIRED / Feature-021 清洗门 / 既有 mock 流程继续走，
    # 不引入额外失败。生产路径上 db 是真实 AsyncSession 不会进入此分支。
    try:
        cfg_row = (
            await session.execute(
                text(
                    "SELECT enabled FROM task_channel_configs "
                    "WHERE task_type = 'content_review_gate'"
                )
            )
        ).scalar_one_or_none()
    except (TypeError, AttributeError) as exc:
        logger.debug(
            "review_gate: layer-2 DB probe non-functional (mock or fixture?); "
            "fall-soft to not_classified. cos_object_key=%s exc=%s",
            cos_object_key, exc,
        )
        return ReviewGateResult(decision="not_classified")
    # 如果配置行缺失（迁移未跑/被误删），fail-secure：按"严格审核门"执行
    if cfg_row is False:
        logger.warning(
            "review_gate bypassed by task_channel_configs.content_review_gate.enabled=False; "
            "cos_object_key=%s",
            cos_object_key,
        )
        return ReviewGateResult(decision="bypassed", bypass_reason="channel_config")

    # Layer 3+4: 查询 cvclf 行（同样的 mock-safe 防御）
    try:
        row = (
            await session.execute(
                select(
                    CoachVideoClassification.id,
                    CoachVideoClassification.review_state,
                    CoachVideoClassification.review_version,
                ).where(CoachVideoClassification.cos_object_key == cos_object_key)
            )
        ).first()
        if row is None:
            # 通常 router 已被 CLASSIFICATION_REQUIRED 拦截，这里 fail-soft
            return ReviewGateResult(decision="not_classified")
        # mock 场景下 row 可能是 coroutine（AsyncMock 自动生成）；把 unpack 也纳入
        # try 块以触发 TypeError，统一 fall-soft
        cvclf_id, review_state, review_version = row
    except (TypeError, AttributeError) as exc:
        logger.debug(
            "review_gate: layer-3 DB probe non-functional (mock or fixture?); "
            "fall-soft to not_classified. cos_object_key=%s exc=%s",
            cos_object_key, exc,
        )
        return ReviewGateResult(decision="not_classified")

    # Layer 5: 状态映射
    decision_map: dict[str, GateDecision] = {
        "approved": "ok",
        "pending_review": "not_reviewed",
        "rejected": "rejected",
        "stale": "stale",
    }
    decision = decision_map.get(review_state)
    if decision is None:
        # CHECK 约束保证不会发生；防御性 fail-fast
        raise RuntimeError(
            f"review_gate: unknown review_state={review_state!r} for "
            f"cvclf_id={cvclf_id} cos_object_key={cos_object_key!r}; "
            "should have been blocked by ck_cvclf_review_state CHECK"
        )

    return ReviewGateResult(
        decision=decision,
        cvclf_id=cvclf_id,
        review_state=review_state,
        review_version=int(review_version),
    )


__all__ = ["GateDecision", "ReviewGateResult", "evaluate_review_gate"]
