"""TechStandard and TechStandardPoint ORM models.

TechStandard: versioned per-technique aggregated standard record.
  - Built by TechStandardBuilder from ExpertTechPoint data.
  - Only one version is 'active' per tech_category at any time.
  - Previous versions are 'archived' when a new build is triggered.

TechStandardPoint: per-dimension standard parameter (one per dimension per version).
  - ideal = median of param_ideal values from valid ExpertTechPoints
  - min   = P25 (25th percentile)
  - max   = P75 (75th percentile)
  - Only dimensions with actual data are created (no placeholder records).
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class StandardStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class SourceQuality(str, enum.Enum):
    multi_source = "multi_source"    # coach_count >= 2
    single_source = "single_source"  # coach_count == 1


class TechStandard(Base):
    """Versioned aggregated standard for a single technique category."""

    __tablename__ = "tech_standards"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tech_category: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[StandardStatus] = mapped_column(
        String(16), nullable=False, default=StandardStatus.active
    )
    source_quality: Mapped[SourceQuality] = mapped_column(
        String(16), nullable=False
    )
    # Number of distinct coaches whose data contributed to this standard
    coach_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Total number of ExpertTechPoints used (across all dimensions)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    built_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )

    # Relationships
    points: Mapped[List["TechStandardPoint"]] = relationship(
        "TechStandardPoint",
        back_populates="standard",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("tech_category", "version", name="uq_ts_tech_version"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_ts_status"),
        CheckConstraint(
            "source_quality IN ('multi_source', 'single_source')",
            name="ck_ts_source_quality",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<TechStandard tech_category={self.tech_category!r} "
            f"version={self.version} status={self.status}>"
        )


class TechStandardPoint(Base):
    """Per-dimension standard parameter within a TechStandard version.

    Aggregation formulas (applied to ExpertTechPoint.param_ideal values):
      ideal = median
      min   = P25
      max   = P75
    """

    __tablename__ = "tech_standard_points"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    standard_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tech_standards.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Matches ExpertTechPoint.dimension (e.g. "elbow_angle_at_contact")
    dimension: Mapped[str] = mapped_column(String(128), nullable=False)
    # Aggregated stats from param_ideal values of valid ExpertTechPoints
    ideal: Mapped[float] = mapped_column(Float, nullable=False)  # median
    min: Mapped[float] = mapped_column(Float, nullable=False)    # P25
    max: Mapped[float] = mapped_column(Float, nullable=False)    # P75
    unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Number of ExpertTechPoints used for this dimension
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Number of distinct coaches contributing to this dimension
    coach_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    standard: Mapped["TechStandard"] = relationship(
        "TechStandard", back_populates="points"
    )

    __table_args__ = (
        UniqueConstraint(
            "standard_id", "dimension", name="uq_tsp_standard_dimension"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<TechStandardPoint standard_id={self.standard_id} "
            f"dimension={self.dimension!r} ideal={self.ideal}>"
        )
