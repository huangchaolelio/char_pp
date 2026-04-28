"""ReferenceVideo ORM model — the assembled reference video for a SkillExecution.

One ReferenceVideo is produced per SkillExecution. It is assembled from
ordered ReferenceVideoSegments, each sourced from an expert video clip.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class ReferenceVideo(Base):
    __tablename__ = "reference_videos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_executions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    kb_version: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("tech_knowledge_bases.version"),
        nullable=False,
    )
    # pending / generating / completed / generation_failed
    generation_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending"
    )
    # COS object key of the final assembled video; NULL until completed
    cos_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_dimensions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    included_dimensions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
    )

    # Relationships
    execution: Mapped["SkillExecution"] = relationship(  # noqa: F821
        "SkillExecution",
        back_populates="reference_video",
    )
    segments: Mapped[list["ReferenceVideoSegment"]] = relationship(  # noqa: F821
        "ReferenceVideoSegment",
        back_populates="reference_video",
        cascade="all, delete-orphan",
        order_by="ReferenceVideoSegment.sequence_order",
    )
