"""TeachingTip ORM model — per-category versioned teaching tip entry (Feature-019).

Feature-019 重构：
  - 新增列 ``tech_category`` / ``kb_tech_category`` + ``kb_version`` / ``status``
  - 删除列 ``action_type``（被 tech_category 取代，语义重复）
  - 复合 FK 绑 ``(tech_knowledge_bases.tech_category, version)``，生命周期与 KB 绑同
  - ``task_id`` 放宽为 nullable（tips 生命周期与 task 解耦）
  - 归档联动：KB approve 时同类别 auto tips 批量激活 / 旧 auto tips 批量归档
    （human tips 不参与批量，保留 Feature-005 的"人工标注不可被自动流覆盖"）
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import text

from src.db.session import Base


class TipStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class TeachingTip(Base):
    __tablename__ = "teaching_tips"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Feature-019 新列
    tech_category: Mapped[str] = mapped_column(String(64), nullable=False)
    kb_tech_category: Mapped[str] = mapped_column(String(64), nullable=False)
    kb_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TipStatus] = mapped_column(
        Enum(TipStatus, name="tip_status_enum", create_type=False),
        nullable=False,
        default=TipStatus.draft,
    )

    # preparation | contact | follow_through | footwork | general
    tech_phase: Mapped[str] = mapped_column(String(30), nullable=False)
    tip_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(10), nullable=False, default="auto"
    )
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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

    # Relationship
    task: Mapped[Optional["AnalysisTask"]] = relationship(  # noqa: F821
        "AnalysisTask",
        foreign_keys=[task_id],
        back_populates="teaching_tips",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["kb_tech_category", "kb_version"],
            ["tech_knowledge_bases.tech_category", "tech_knowledge_bases.version"],
            ondelete="CASCADE",
            name="fk_teaching_tips_kb",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_teaching_tip_confidence_range",
        ),
        CheckConstraint(
            "source_type IN ('auto', 'human')",
            name="ck_teaching_tip_source_type",
        ),
        Index("ix_teaching_tips_task_id", "task_id"),
        Index("ix_teaching_tips_tech_category", "tech_category"),
        Index("ix_teaching_tips_status", "status"),
        Index("ix_teaching_tips_kb", "kb_tech_category", "kb_version"),
        Index("ix_teaching_tips_source_type", "source_type"),
    )
