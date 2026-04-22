"""VideoClassification ORM model — persistent classification record for COS teaching videos."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Float, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.db.session import Base


class VideoClassification(Base):
    """Stores the classification result for a single COS teaching video.

    Primary key is the full COS object key (unique per video).
    ``manually_overridden`` records are never overwritten by the automated
    refresh process — only explicit PATCH calls can update them.
    """

    __tablename__ = "video_classifications"

    # Primary key — full COS path uniquely identifies a video
    cos_object_key: Mapped[str] = mapped_column(String(500), primary_key=True)

    # Coach info parsed from COS path
    coach_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Three-level classification hierarchy
    tech_category: Mapped[str] = mapped_column(String(50), nullable=False)
    tech_sub_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tech_detail: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # tutorial = technique explanation; training = drill / practice plan
    video_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Mapped ActionType enum value, null when no matching enum exists
    # (e.g. serve, footwork, receive categories)
    action_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 1.0 = precise keyword match | 0.7 = category-level match | 0.5 = fallback
    classification_confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Human override fields
    manually_overridden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    classified_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
