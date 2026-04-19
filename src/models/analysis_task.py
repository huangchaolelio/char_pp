"""AnalysisTask ORM model — records a single video analysis request."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Enum, Float, ForeignKey, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.db.encryption import EncryptedString
from src.db.session import Base


class TaskType(str, enum.Enum):
    expert_video = "expert_video"
    athlete_video = "athlete_video"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    success = "success"
    failed = "failed"
    rejected = "rejected"


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_type: Mapped[TaskType] = mapped_column(
        Enum(TaskType, name="task_type_enum"), nullable=False
    )
    video_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    video_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    video_duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    video_fps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    video_resolution: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Stored encrypted at application layer via AES-256-GCM (T043)
    video_storage_uri: Mapped[str] = mapped_column(EncryptedString(1000), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status_enum"),
        nullable=False,
        default=TaskStatus.pending,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    knowledge_base_version: Mapped[Optional[str]] = mapped_column(
        String(20),
        ForeignKey("tech_knowledge_bases.version", ondelete="SET NULL"),
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # Soft-delete: set by DELETE endpoint; physical cleanup runs daily
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships
    expert_tech_points: Mapped[list["ExpertTechPoint"]] = relationship(  # noqa: F821
        "ExpertTechPoint",
        foreign_keys="ExpertTechPoint.source_video_id",
        back_populates="source_task",
        cascade="all, delete-orphan",
    )
    athlete_motion_analyses: Mapped[list["AthleteMotionAnalysis"]] = relationship(  # noqa: F821
        "AthleteMotionAnalysis",
        back_populates="task",
        cascade="all, delete-orphan",
    )
    coaching_advice: Mapped[list["CoachingAdvice"]] = relationship(  # noqa: F821
        "CoachingAdvice",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
