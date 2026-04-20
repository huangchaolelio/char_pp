"""ExpertTechPoint ORM model — immutable record of a single technical dimension.

Write-once: once inserted, records are never updated. Knowledge base updates
create new version records instead (append-only versioning).

Unique constraint: (knowledge_base_version, action_type, dimension)
Validation: param_min ≤ param_ideal ≤ param_max
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.db.session import Base


class ActionType(str, enum.Enum):
    forehand_topspin = "forehand_topspin"
    backhand_push = "backhand_push"


class ExpertTechPoint(Base):
    __tablename__ = "expert_tech_points"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    knowledge_base_version: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("tech_knowledge_bases.version", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[ActionType] = mapped_column(
        Enum(ActionType, name="action_type_enum"), nullable=False
    )
    # e.g. "elbow_angle", "swing_trajectory", "contact_timing", "weight_transfer"
    dimension: Mapped[str] = mapped_column(String(100), nullable=False)
    param_min: Mapped[float] = mapped_column(Float, nullable=False)
    param_max: Mapped[float] = mapped_column(Float, nullable=False)
    param_ideal: Mapped[float] = mapped_column(Float, nullable=False)
    # e.g. "°", "ms", "ratio"
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    # Only points with confidence >= 0.7 are inserted (enforced in tech_extractor)
    extraction_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Feature 002: audio-enhanced extraction fields
    # Source of this tech point: visual / audio / visual+audio
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="visual"
    )
    # FK to the TechSemanticSegment that contributed this point (audio/subtitle source)
    transcript_segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tech_semantic_segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # True when visual and audio sources disagree beyond threshold (param diff > 15%)
    conflict_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # JSONB detail when conflict_flag=True: {"visual": {...}, "audio": {...}, "diff_pct": 0.18}
    conflict_detail: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    knowledge_base: Mapped["TechKnowledgeBase"] = relationship(  # noqa: F821
        "TechKnowledgeBase", back_populates="tech_points"
    )
    source_task: Mapped["AnalysisTask"] = relationship(  # noqa: F821
        "AnalysisTask",
        foreign_keys=[source_video_id],
        back_populates="expert_tech_points",
    )
    transcript_segment: Mapped[Optional["TechSemanticSegment"]] = relationship(  # noqa: F821
        "TechSemanticSegment",
        foreign_keys=[transcript_segment_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_version",
            "action_type",
            "dimension",
            name="uq_expert_point_version_action_dim",
        ),
        CheckConstraint(
            "param_min <= param_ideal AND param_ideal <= param_max",
            name="ck_expert_point_param_range",
        ),
        CheckConstraint(
            "extraction_confidence >= 0.0 AND extraction_confidence <= 1.0",
            name="ck_expert_point_confidence_range",
        ),
    )
