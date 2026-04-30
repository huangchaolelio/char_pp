"""SkillExecution ORM model — records a single run of a Skill to produce a ReferenceVideo.

Each execution captures a snapshot of the skill config at run time so the
generated reference video is reproducible even if the skill config changes later.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Enum, ForeignKey, ForeignKeyConstraint, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class ExecutionStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    approved = "approved"
    rejected = "rejected"


class SkillExecution(Base):
    __tablename__ = "skill_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus, name="execution_status_enum"),
        nullable=False,
        default=ExecutionStatus.pending,
    )
    # Snapshot of skill config at execution time — used for reproducibility
    skill_config_snapshot: Mapped[dict] = mapped_column(JSONB(), nullable=False)
    # Feature-019: 复合 FK → (tech_knowledge_bases.tech_category, version)
    kb_tech_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    kb_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
        onupdate=text("timezone('Asia/Shanghai', now())"),
    )

    # Relationships
    skill: Mapped["Skill"] = relationship(  # noqa: F821
        "Skill",
        back_populates="executions",
    )
    reference_video: Mapped[Optional["ReferenceVideo"]] = relationship(  # noqa: F821
        "ReferenceVideo",
        back_populates="execution",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["kb_tech_category", "kb_version"],
            ["tech_knowledge_bases.tech_category", "tech_knowledge_bases.version"],
            ondelete="SET NULL",
            name="fk_skill_executions_kb",
        ),
    )
