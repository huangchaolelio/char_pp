"""CoachVideoClassification ORM model — tech classification record for a coach video.

Each record represents one .mp4 file under COS_VIDEO_ALL_COCAH path,
with its detected tech_category and metadata.

classification_source lifecycle:
  - 'rule': keyword-matched classification (confidence=1.0)
  - 'llm': LLM-inferred classification (confidence from LLM)
  - 'manual': human-corrected via PATCH API (confidence=1.0, irreversible)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    Index,
    Integer,
    String,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, TEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.db.session import Base


class CoachVideoClassification(Base):
    __tablename__ = "coach_video_classifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    coach_name: Mapped[str] = mapped_column(String(100), nullable=False)
    course_series: Mapped[str] = mapped_column(String(255), nullable=False)
    cos_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    tech_category: Mapped[str] = mapped_column(String(64), nullable=False)
    tech_tags: Mapped[list[str]] = mapped_column(
        ARRAY(TEXT()), nullable=False, server_default="{}"
    )
    raw_tech_desc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    classification_source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="rule"
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    duration_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    name_source: Mapped[str] = mapped_column(String(10), nullable=False, default="map")
    kb_extracted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("cos_object_key", name="uq_cvclf_cos_object_key"),
        CheckConstraint(
            "classification_source IN ('rule', 'llm', 'manual')",
            name="ck_cvclf_source",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_cvclf_confidence_range",
        ),
        CheckConstraint(
            "name_source IN ('map', 'fallback')",
            name="ck_cvclf_name_source",
        ),
        Index("idx_cvclf_coach", "coach_name"),
        Index("idx_cvclf_tech", "tech_category"),
        Index("idx_cvclf_kb", "kb_extracted"),
        Index("idx_cvclf_coach_tech", "coach_name", "tech_category"),
    )
