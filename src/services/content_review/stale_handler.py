"""Feature-022 内容审核 — stale 状态处理器.

由 :mod:`src.services.curation.curation_service` 在清洗作业 ``status='success'``
回调中调用，根据数据模型 § 3.4 状态机执行：

  approved + 重新清洗成功 → stale → pending_review
  rejected + 重新清洗成功 → 保持 rejected（澄清 Q5：拒绝条目永久保留）
  pending_review / stale + 重新清洗成功 → 保持原状态（更新 last_curation_job_id 已由 curation_service 完成）

本模块**只**负责审核状态机的转移；``coach_video_classifications.last_curation_job_id``
和 ``low_quality`` 由 curation_service 已经在同一事务内 update 完毕。

关键约束（spec.md FR-011 + FR-011a + 澄清 Q3）：
  · 状态变更必须 ``review_version += 1``
  · 进入 stale 后：``pending_since = now()``（让积压告警 + 平均等待时延能感知重审等待）
  · stale → pending_review 是**同一事务内的复合迁移**：直接落 ``pending_review``
    + ``pending_since=now()``，不暴露中间 stale 态给客户端
  · 把当前指向旧决策的 ``last_decision_id`` 保留（用于审计回溯），并把对应
    决策行的 ``superseded_at = now()``（标记已被新清洗覆盖）

详见：
    specs/022-content-review-workflow/data-model.md § 3.4 / § 4
    specs/022-content-review-workflow/research.md R4
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.coach_video_classification import CoachVideoClassification
from src.models.content_review_decision import ContentReviewDecision
from src.utils.time_utils import now_cst


if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


async def mark_stale_after_recurate(
    session: AsyncSession,
    *,
    cvclf_id: UUID,
    new_curation_job_id: UUID,
) -> str | None:
    """在清洗 success 回调中执行审核状态迁移.

    必须由调用方（curation_service）在已经更新过 ``last_curation_job_id`` /
    ``low_quality`` 之后、同一 session/transaction 内调用；本函数不会
    自行 commit，由调用方统一 commit。

    Args:
        session: SQLAlchemy AsyncSession（调用方持有）
        cvclf_id: 受影响的 ``coach_video_classifications`` 主键
        new_curation_job_id: 本次刚成功的清洗作业 id（仅用于日志留痕，不落库）

    Returns:
        - ``None`` —— 无需迁移（rejected 永久保留 / pending_review 保持 / stale 保持）
        - ``"approved_to_pending_review"`` —— 已发生迁移；调用方可据此打 metric
    """
    # 锁定该行直到事务结束，避免并发清洗与并发审核决策互相覆盖
    row = (
        await session.execute(
            select(CoachVideoClassification)
            .where(CoachVideoClassification.id == cvclf_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        # 极端情况：清洗过程中 cvclf 行被删除；fail-soft 仅日志告警
        logger.warning(
            "stale_handler: cvclf row vanished mid-recurate; "
            "cvclf_id=%s new_curation_job_id=%s",
            cvclf_id, new_curation_job_id,
        )
        return None

    state = row.review_state

    if state == "rejected":
        # 澄清 Q5：拒绝条目永久保留 rejected 状态；如需重审需运营手动 reset。
        # 此处不动状态机，但保留日志便于运维事后定位（哪些 rejected 条目被重洗过）
        logger.info(
            "stale_handler: skip rejected (Q5 perm-reject); "
            "cvclf_id=%s new_curation_job_id=%s review_version=%d",
            cvclf_id, new_curation_job_id, row.review_version,
        )
        return None

    if state == "pending_review":
        # 还没决策过；保持 pending_review，仅 last_curation_job_id 已被 curation_service 更新。
        # 注意：这里不更新 pending_since（保留首次 pending_since 用于"等待 SLA 计算"，
        # 否则重新清洗会让等待时延归零，掩盖积压问题）。
        logger.info(
            "stale_handler: skip pending_review (no decision yet); "
            "cvclf_id=%s new_curation_job_id=%s",
            cvclf_id, new_curation_job_id,
        )
        return None

    if state == "stale":
        # 已经在等待重审，无需再迁移；仅日志说明本次清洗叠加在已 stale 状态上
        logger.info(
            "stale_handler: skip stale (already awaiting re-review); "
            "cvclf_id=%s new_curation_job_id=%s",
            cvclf_id, new_curation_job_id,
        )
        return None

    if state != "approved":
        # CHECK 约束保证不会出现其它值；防御性日志
        logger.error(
            "stale_handler: unexpected review_state=%r for cvclf_id=%s; "
            "expected approved",
            state, cvclf_id,
        )
        return None

    # 主路径：approved → pending_review（合并 approved → stale → pending_review，
    # 不暴露中间 stale 态；详见 data-model.md § 3.4 注释）
    now = now_cst()

    # 1) 把指向旧决策的 ``last_decision_id`` 对应行标记 superseded_at；
    #    保留 last_decision_id 不变，用于审计回溯（运营追问"为何重新审核"时可看历史）
    if row.last_decision_id is not None:
        await session.execute(
            update(ContentReviewDecision)
            .where(
                ContentReviewDecision.id == row.last_decision_id,
                ContentReviewDecision.superseded_at.is_(None),
            )
            .values(superseded_at=now)
        )

    # 2) 主表迁移：approved → pending_review（FR-011a：不暴露 stale 中间态）
    new_version = int(row.review_version) + 1
    await session.execute(
        update(CoachVideoClassification)
        .where(CoachVideoClassification.id == cvclf_id)
        .values(
            review_state="pending_review",
            review_version=new_version,
            pending_since=now,
        )
    )

    logger.info(
        "stale_handler: approved→pending_review (re-review) "
        "cvclf_id=%s new_curation_job_id=%s old_decision_id=%s "
        "review_version=%d→%d",
        cvclf_id, new_curation_job_id, row.last_decision_id,
        row.review_version, new_version,
    )

    return "approved_to_pending_review"


__all__ = ["mark_stale_after_recurate"]
