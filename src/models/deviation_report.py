"""DeviationReport ORM model — comparison of athlete motion vs expert standard.

Each record represents the deviation of one measured dimension against the
corresponding ExpertTechPoint ideal value. One record per dimension per
action segment.

Stability rule (computed by deviation_analyzer, not at insert time):
  - is_stable_deviation = True  if ≥3 same action/dimension samples and ≥70% show deviation
  - is_stable_deviation = False if ≥3 samples but < 70% show deviation
  - is_stable_deviation = None  if < 3 samples (NULL = insufficient data)
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Enum, Float, ForeignKey, String, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class DeviationDirection(str, enum.Enum):
    above = "above"   # measured > param_max
    below = "below"   # measured < param_min
    none = "none"     # within [param_min, param_max]


class DeviationReport(Base):
    __tablename__ = "deviation_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("athlete_motion_analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    expert_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expert_tech_points.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dimension: Mapped[str] = mapped_column(String(100), nullable=False)
    measured_value: Mapped[float] = mapped_column(Float, nullable=False)
    ideal_value: Mapped[float] = mapped_column(Float, nullable=False)
    deviation_value: Mapped[float] = mapped_column(Float, nullable=False)
    deviation_direction: Mapped[DeviationDirection] = mapped_column(
        Enum(DeviationDirection, name="deviation_direction_enum"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    is_low_confidence: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # NULL = insufficient samples; True/False set after stability aggregation
    is_stable_deviation: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # [0,1] normalized impact score; NULL until computed
    impact_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )

    # Relationships
    analysis: Mapped["AthleteMotionAnalysis"] = relationship(  # noqa: F821
        "AthleteMotionAnalysis",
        back_populates="deviation_reports",
    )
    expert_point: Mapped["ExpertTechPoint"] = relationship(  # noqa: F821
        "ExpertTechPoint",
        foreign_keys=[expert_point_id],
    )
    coaching_advice: Mapped[list["CoachingAdvice"]] = relationship(  # noqa: F821
        "CoachingAdvice",
        back_populates="deviation",
        cascade="all, delete-orphan",
    )
