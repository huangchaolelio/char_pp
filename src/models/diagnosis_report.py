"""DiagnosisReport and DiagnosisDimensionResult ORM models.

Feature 011: Amateur motion diagnosis
- DiagnosisReport: one anonymous diagnosis session (UUID PK = request_id)
- DiagnosisDimensionResult: per-dimension comparison detail for a report
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.db.session import Base


class DeviationLevel(str, enum.Enum):
    ok = "ok"                   # within [min, max]
    slight = "slight"           # between 1x and 1.5x half-width outside range
    significant = "significant" # beyond 1.5x half-width outside range


class DeviationDirection(str, enum.Enum):
    above = "above"   # measured > max
    below = "below"   # measured < min
    none = "none"     # within range


class DiagnosisReport(Base):
    __tablename__ = "diagnosis_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tech_category: Mapped[str] = mapped_column(String(64), nullable=False)
    standard_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tech_standards.id", ondelete="RESTRICT"),
        nullable=False,
    )
    standard_version: Mapped[int] = mapped_column(nullable=False)
    video_path: Mapped[str] = mapped_column(Text, nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    # JSON list of dimension names that are within standard, e.g. '["elbow_angle"]'
    strengths_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    dimensions: Mapped[List["DiagnosisDimensionResult"]] = relationship(
        "DiagnosisDimensionResult",
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class DiagnosisDimensionResult(Base):
    __tablename__ = "diagnosis_dimension_results"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("diagnosis_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    dimension: Mapped[str] = mapped_column(String(128), nullable=False)
    measured_value: Mapped[float] = mapped_column(Float, nullable=False)
    ideal_value: Mapped[float] = mapped_column(Float, nullable=False)
    standard_min: Mapped[float] = mapped_column(Float, nullable=False)
    standard_max: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    deviation_level: Mapped[str] = mapped_column(String(20), nullable=False)
    deviation_direction: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    improvement_advice: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    report: Mapped["DiagnosisReport"] = relationship(
        "DiagnosisReport", back_populates="dimensions"
    )

    __table_args__ = (
        UniqueConstraint("report_id", "dimension", name="uq_ddr_report_dimension"),
        CheckConstraint(
            "deviation_level IN ('ok', 'slight', 'significant')",
            name="ck_ddr_deviation_level",
        ),
        CheckConstraint(
            "deviation_direction IN ('above', 'below', 'none') OR deviation_direction IS NULL",
            name="ck_ddr_deviation_direction",
        ),
    )
