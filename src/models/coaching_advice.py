"""CoachingAdvice ORM model — actionable improvement recommendations.

One CoachingAdvice record is generated per deviation (deviation_direction ≠ none).
Records are sorted by impact_score DESC when returned to the caller.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Enum, Float, ForeignKey, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class ReliabilityLevel(str, enum.Enum):
    high = "high"  # confidence >= 0.7
    low = "low"    # confidence < 0.7, reliability_note is required


class CoachingAdvice(Base):
    __tablename__ = "coaching_advice"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    deviation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deviation_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    deviation_description: Mapped[str] = mapped_column(Text, nullable=False)
    improvement_target: Mapped[str] = mapped_column(Text, nullable=False)
    improvement_method: Mapped[str] = mapped_column(Text, nullable=False)
    impact_score: Mapped[float] = mapped_column(Float, nullable=False)
    reliability_level: Mapped[ReliabilityLevel] = mapped_column(
        Enum(ReliabilityLevel, name="reliability_level_enum"), nullable=False
    )
    # Required when reliability_level = low
    reliability_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )

    # Relationships
    deviation: Mapped["DeviationReport"] = relationship(  # noqa: F821
        "DeviationReport",
        back_populates="coaching_advice",
    )
    task: Mapped["AnalysisTask"] = relationship(  # noqa: F821
        "AnalysisTask",
        back_populates="coaching_advice",
    )
