"""TeachingTip ORM model — a single teaching advice entry extracted from expert video audio.

Each record represents one coaching tip extracted by LLM from audio transcript,
grouped by tech_phase (preparation/contact/follow_through/footwork/general).

source_type lifecycle:
  - 'auto': LLM-generated, can be replaced on re-trigger
  - 'human': manually reviewed/edited, preserved on re-trigger (irreversible)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class TeachingTip(Base):
    __tablename__ = "teaching_tips"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Matches ActionType enum values (stored as string for flexibility)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # preparation | contact | follow_through | footwork | general
    tech_phase: Mapped[str] = mapped_column(String(30), nullable=False)
    # The teaching tip text content (Chinese)
    tip_text: Mapped[str] = mapped_column(Text, nullable=False)
    # LLM extraction confidence [0.0, 1.0]
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # 'auto' (LLM-generated) or 'human' (manually reviewed/edited)
    source_type: Mapped[str] = mapped_column(
        String(10), nullable=False, default="auto"
    )
    # Preserved original AI text when source_type changed to 'human'
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
        onupdate=text("timezone('Asia/Shanghai', now())"),
    )

    # Relationship
    task: Mapped["AnalysisTask"] = relationship(  # noqa: F821
        "AnalysisTask",
        foreign_keys=[task_id],
        back_populates="teaching_tips",
    )

    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_teaching_tip_confidence_range",
        ),
        CheckConstraint(
            "source_type IN ('auto', 'human')",
            name="ck_teaching_tip_source_type",
        ),
        Index("ix_teaching_tips_task_id", "task_id"),
        Index("ix_teaching_tips_action_type", "action_type"),
        Index("ix_teaching_tips_source_type", "source_type"),
    )
