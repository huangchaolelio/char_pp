"""TechKnowledgeBase ORM model — per-category versioned KB record.

Feature-019 重构：主键由单列 ``version VARCHAR`` 改为复合主键
``(tech_category, version INTEGER)``；每个 tech_category 维度独立走
``draft → active → archived`` 状态机，通过 partial unique index 强约束
每类别唯一 active。

- 单 active 约束（每类别）：``uq_tech_kb_active_per_category`` partial unique index
- 与 extraction_jobs 的关系：``extraction_job_id NOT NULL FK``（每条 KB 必可回溯到产出作业）
- 与 teaching_tips / expert_tech_points 等表的 FK：复合键 ``(tech_category, version)``
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import text

from src.db.session import Base
from src.models.analysis_task import BusinessPhase


class KBStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class TechKnowledgeBase(Base):
    __tablename__ = "tech_knowledge_bases"

    # ── 复合主键（Feature-019）─────────────────────────────────────────
    tech_category: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[KBStatus] = mapped_column(
        Enum(KBStatus, name="kb_status_enum", create_type=False),
        nullable=False,
        default=KBStatus.draft,
    )
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Feature-019 强化：NOT NULL；每条 KB 必可回溯到其产出作业
    extraction_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )

    approved_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
    )

    # Feature-018 遗留（业务阶段/步骤标签；值恒为 STANDARDIZATION / kb_version_activate）
    business_phase: Mapped[BusinessPhase] = mapped_column(
        Enum(BusinessPhase, name="business_phase_enum", create_type=False),
        nullable=False,
    )
    business_step: Mapped[str] = mapped_column(String(64), nullable=False)

    # Relationships
    tech_points: Mapped[list["ExpertTechPoint"]] = relationship(  # noqa: F821
        "ExpertTechPoint",
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        PrimaryKeyConstraint("tech_category", "version", name="pk_tech_kb_cat_ver"),
        CheckConstraint("version >= 1", name="ck_tech_kb_version_positive"),
        CheckConstraint("point_count >= 0", name="ck_tech_kb_point_count_nn"),
        Index("idx_tech_kb_extraction_job", "extraction_job_id"),
        Index("idx_tech_kb_status", "status"),
        # partial unique index `uq_tech_kb_active_per_category` 由迁移 0017 直接 `op.execute`
        # 创建（SQLAlchemy 的 Index 不支持 WHERE 子句的方言中立表达），此处仅声明意图
    )
