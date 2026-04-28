"""Skill ORM model — defines a reusable skill with video source configuration.

A Skill represents a named coaching skill (e.g. "forehand topspin correction")
that references a set of source videos and configuration used to generate
reference videos for athlete feedback.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ARRAY, Boolean, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # e.g. ["forehand_topspin", "backhand_push"]
    action_types: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False
    )
    # {"type": "cos_prefix"|"task_ids", "value": "path/prefix/"|["uuid1","uuid2",...]}
    video_source_config: Mapped[dict] = mapped_column(JSONB(), nullable=False)
    enable_audio: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    audio_language: Mapped[str] = mapped_column(
        String(10), nullable=False, default="zh"
    )
    # Extensible per-skill configuration (thresholds, segment strategy, etc.)
    extra_config: Mapped[dict] = mapped_column(
        JSONB(), nullable=False, server_default="{}"
    )
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )

    # Relationships
    executions: Mapped[list["SkillExecution"]] = relationship(  # noqa: F821
        "SkillExecution",
        back_populates="skill",
        cascade="all, delete-orphan",
    )
