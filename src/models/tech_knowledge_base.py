"""TechKnowledgeBase ORM model — versioned collection of expert tech points.

State machine: draft → active → archived
Constraint: at most one version may have status='active' at any time.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Enum, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import ARRAY
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
    )
