"""AnalysisTask ORM model — records a single video analysis request."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Enum, Float, ForeignKey, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

if TYPE_CHECKING:
    from src.models.coach import Coach

from src.db.encryption import EncryptedString
from src.db.session import Base


class TaskType(str, enum.Enum):
    """Feature 013 — three mutually exclusive task channels.

    Prior values (``expert_video`` / ``athlete_video``) are removed by
    Alembic 0012; no in-place mapping exists.

    Feature-016 adds ``video_preprocessing`` as a fourth channel.
    """

    video_classification = "video_classification"
    kb_extraction = "kb_extraction"
    athlete_diagnosis = "athlete_diagnosis"
    video_preprocessing = "video_preprocessing"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    success = "success"
    partial_success = "partial_success"
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

    # Feature 002: long video progress tracking
    total_segments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    processed_segments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    progress_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    audio_fallback_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    # Soft-delete: set by DELETE endpoint; physical cleanup runs daily
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )

    # Feature 007: per-task processing timing
    timing_stats: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Feature 006: multi-coach KB
    coach_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Feature 013 — Task pipeline redesign
    # COS object key for deduplication and idempotency keying;
    # present for classification & kb_extraction rows, NULL for athlete_diagnosis.
    cos_object_key: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    # How this row entered the pipeline: 'single' | 'batch' | 'scan'
    submitted_via: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="single", default="single"
    )
    # Only set when submitted_via='scan' — points back to the scan_cos_videos task row.
    parent_scan_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Feature 014 — KB extraction pipeline: links a kb_extraction task to its
    # DAG ``extraction_jobs`` row (1:1). NULL for non-kb_extraction rows and
    # for legacy Feature-013 stub rows created before Feature-014.
    extraction_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    coach: Mapped[Optional["Coach"]] = relationship(
        "Coach",
        back_populates="tasks",
        foreign_keys=[coach_id],
    )
    expert_tech_points: Mapped[list["ExpertTechPoint"]] = relationship(  # noqa: F821
        "ExpertTechPoint",
        foreign_keys="ExpertTechPoint.source_video_id",
        back_populates="source_task",
        cascade="all, delete-orphan",
    )
    audio_transcript: Mapped[Optional["AudioTranscript"]] = relationship(  # noqa: F821
        "AudioTranscript",
        back_populates="task",
        cascade="all, delete-orphan",
        uselist=False,
    )
    tech_semantic_segments: Mapped[list["TechSemanticSegment"]] = relationship(  # noqa: F821
        "TechSemanticSegment",
        foreign_keys="TechSemanticSegment.task_id",
        back_populates="task",
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
    teaching_tips: Mapped[list["TeachingTip"]] = relationship(  # noqa: F821
        "TeachingTip",
        foreign_keys="TeachingTip.task_id",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
