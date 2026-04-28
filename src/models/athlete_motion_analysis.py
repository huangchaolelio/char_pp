"""AthleteMotionAnalysis ORM model — structured result for one action segment.

Each record represents a single action clip detected in an athlete's video.
Measured parameters are stored as JSONB:
  {"dimension": {"value": float, "unit": str, "confidence": float}}
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class AthleteActionType(str, enum.Enum):
    forehand_topspin = "forehand_topspin"
    backhand_push = "backhand_push"
    unknown = "unknown"


class AthleteMotionAnalysis(Base):
    __tablename__ = "athlete_motion_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[AthleteActionType] = mapped_column(
        Enum(AthleteActionType, name="athlete_action_type_enum"), nullable=False
    )
    segment_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # Structure: {"dimension": {"value": float, "unit": str, "confidence": float}}
    measured_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    is_low_confidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    knowledge_base_version: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("tech_knowledge_bases.version", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )

    # Relationships
    task: Mapped["AnalysisTask"] = relationship(  # noqa: F821
        "AnalysisTask",
        back_populates="athlete_motion_analyses",
    )
    knowledge_base: Mapped["TechKnowledgeBase"] = relationship(  # noqa: F821
        "TechKnowledgeBase",
        foreign_keys=[knowledge_base_version],
    )
    deviation_reports: Mapped[list["DeviationReport"]] = relationship(  # noqa: F821
        "DeviationReport",
        back_populates="analysis",
        cascade="all, delete-orphan",
    )
