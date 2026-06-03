"""VideoClassification ORM model — persistent classification record for COS teaching videos."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKeyConstraint, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy import text

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

    # Feature-023：严格四级分类字段（取代旧 tech_category / tech_sub_category / tech_detail）
    category_l1: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    category_l2: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    category_l3: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # tutorial = technique explanation; training = drill / practice plan
    video_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Mapped V2 action value, null when no matching dictionary entry exists
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
        TIMESTAMP(timezone=False), server_default=text("timezone('Asia/Shanghai', now())"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        server_default=text("timezone('Asia/Shanghai', now())"),
        onupdate=text("timezone('Asia/Shanghai', now())"),
        nullable=False,
    )

    __table_args__ = (
        # Feature-023 复合外键 → tech_actions 字典（NULLABLE）
        ForeignKeyConstraint(
            ["category_l1", "category_l2", "category_l3", "action"],
            [
                "tech_actions.category_l1",
                "tech_actions.category_l2",
                "tech_actions.category_l3",
                "tech_actions.action",
            ],
            onupdate="CASCADE",
            ondelete="RESTRICT",
            name="fk_vclf_action",
        ),
    )
