"""TechKnowledgeBase ORM model — versioned collection of expert tech points.

State machine: draft → active → archived
Constraint: at most one version may have status='active' at any time.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class KBStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class TechKnowledgeBase(Base):
    __tablename__ = "tech_knowledge_bases"

    # Semantic version string, e.g. "1.0.0"
    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    action_types_covered: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False
    )
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[KBStatus] = mapped_column(
        Enum(KBStatus, name="kb_status_enum"),
        nullable=False,
        default=KBStatus.draft,
    )
    approved_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 迁移 0015 / 方案 A1：直接 FK 回溯产生此 KB 版本的 extraction_job，
    # 取代原先把 job_id 塞进 notes 字符串的做法（不可查询、不可索引）。
    # 允许 NULL：部分历史版本（Feature-002/004 生成）没有对应的 extraction_job。
    extraction_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    tech_points: Mapped[list["ExpertTechPoint"]] = relationship(  # noqa: F821
        "ExpertTechPoint",
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Enforce valid semantic version format at DB level (basic check)
        CheckConstraint(
            "version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name="ck_kb_version_semver",
        ),
        Index("idx_tech_kb_extraction_job", "extraction_job_id"),
    )
