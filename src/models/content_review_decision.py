"""ContentReviewDecision ORM model — Feature-022 内容审核决策留痕.

每条决策对应审核员对一条 ``coach_video_classifications`` 的"通过 / 拒绝"判定。
- 主表 ``coach_video_classifications`` 通过 ``last_decision_id`` 反向指向最近一次决策（循环 FK）
- 审核状态机变更（pending_review → approved/rejected；approved → stale → pending_review）
  必须**在同一个事务**内完成：
    1. INSERT 一行 ``ContentReviewDecision``
    2. UPDATE 既有"未失效"决策行 ``superseded_at = now()``（如有）
    3. UPDATE 主表 ``review_state`` / ``last_decision_id`` / ``review_version += 1`` /
       ``pending_since`` (置 NULL 或 now())
- ``superseded_at`` 一旦写入禁止修改（应用层强制：仅允许 NULL → 时间戳）

详见：
    specs/022-content-review-workflow/data-model.md § 4
    docs/business-workflow.md § 3.6
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import TEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import text

from src.db.session import Base


class ContentReviewDecision(Base):
    """一条审核决策（``approved`` / ``rejected``）的留痕记录."""

    __tablename__ = "content_review_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    cvclf_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "coach_video_classifications.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # 决策做出时绑定的清洗版本（拷贝自主表 ``last_curation_job_id``）；
    # 清洗版本被删除时置 NULL，仍保留决策本体作为审计证据。
    cleansing_version: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "video_curation_jobs.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    # 应用层枚举：approved / rejected（DB CHECK 约束等价限制）
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    # 应用层枚举：quality_low / tech_irrelevant / coach_unauthorized /
    # content_duplicated / other（不入 DB enum 以便后续扩展）。
    # decision='rejected' 时必填；'approved' 时必空（应用层）。
    reason_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(TEXT(), nullable=True)
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
    )
    # 后续决策覆盖本决策时填写（用于审计 + 重洗后批量标记 stale）；
    # 一旦写入只读，应用层强制不可逆。
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_crd_decision",
        ),
        CheckConstraint(
            "(decision = 'rejected' AND reason_code IS NOT NULL) "
            "OR decision = 'approved'",
            name="ck_crd_rejected_requires_reason",
        ),
        Index(
            "idx_crd_cvclf_decided",
            "cvclf_id",
            "decided_at",
        ),
        Index(
            "idx_crd_decided_at",
            "decided_at",
        ),
        Index(
            "idx_crd_reviewer_decided",
            "reviewer_id",
            "decided_at",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ContentReviewDecision id={self.id} "
            f"cvclf_id={self.cvclf_id} decision={self.decision} "
            f"reviewer_id={self.reviewer_id}>"
        )
