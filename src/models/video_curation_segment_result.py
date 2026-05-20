"""VideoCurationSegmentResult ORM — Feature-021 单分段清洗判定 + 人工覆盖留痕.

每行 1 个分段；与 ``video_preprocessing_segments.segment_index`` 软关联（不建跨表 FK
是有意为之：``force=true`` 重跑时分段索引可能因预处理重切而不一致，强外键反而报错）。

设计要点（见 specs/021-video-content-curation/data-model.md §2.2）：

- ``effective_decision`` 是 PostgreSQL ``GENERATED ALWAYS AS ... STORED`` 计算列：
  ``COALESCE(override_decision, auto_decision)``。任何对 ``override_decision`` 的 UPDATE
  自动同步；查询永远走计算列，杜绝应用层漏算
- ``auto_decision`` 落库后不可变；人工覆盖只动 ``override_*`` 4 列与 ``overridden_at``
- ``dim_breakdown`` JSONB 字段保存规则路 5 维各自得分 + 命中关键词，**事后可审计**
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    TIMESTAMP,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import text

from src.db.session import Base

if TYPE_CHECKING:
    from src.models.video_curation_job import VideoCurationJob


class VideoCurationSegmentResult(Base):
    """单分段清洗判定 + 覆盖留痕。"""

    __tablename__ = "video_curation_segment_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_curation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── 自动决策（不可变） ─────────────────────────────────────────
    auto_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    validity_score: Mapped[float] = mapped_column(Float, nullable=False)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    decision_source: Mapped[str] = mapped_column(String(16), nullable=False)
    dim_breakdown: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # ── 人工覆盖（同行扩展） ───────────────────────────────────────
    override_decision: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    override_user: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    overridden_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )

    # ── 计算列：永远等于 COALESCE(override_decision, auto_decision) ──
    # SQLAlchemy 端用 ``Computed(persisted=True)`` 映射 PostgreSQL ``GENERATED STORED``。
    effective_decision: Mapped[str] = mapped_column(
        String(16),
        Computed(
            "COALESCE(override_decision, auto_decision)",
            persisted=True,
        ),
        nullable=False,
    )

    # ── 时间戳 ─────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
        onupdate=text("timezone('Asia/Shanghai', now())"),
    )

    # ── 关系 ─────────────────────────────────────────────────
    job: Mapped["VideoCurationJob"] = relationship(
        "VideoCurationJob",
        back_populates="segment_results",
        lazy="joined",
    )

    __table_args__ = (
        CheckConstraint(
            "auto_decision IN ('accepted','rejected','uncertain')",
            name="ck_curation_seg_auto_decision",
        ),
        CheckConstraint(
            "override_decision IS NULL OR override_decision IN ('accepted','rejected')",
            name="ck_curation_seg_override_decision",
        ),
        CheckConstraint(
            "decision_source IN ('rule','llm')",
            name="ck_curation_seg_decision_source",
        ),
        CheckConstraint(
            "validity_score >= 0 AND validity_score <= 1",
            name="ck_curation_seg_validity_score",
        ),
        UniqueConstraint(
            "job_id",
            "segment_index",
            name="uq_curation_segment",
        ),
        Index("ix_curation_seg_job", "job_id"),
        Index("ix_curation_seg_effective", "job_id", "effective_decision"),
        Index(
            "ix_curation_seg_overridden_at",
            "overridden_at",
            postgresql_where=text("overridden_at IS NOT NULL"),
        ),
    )
