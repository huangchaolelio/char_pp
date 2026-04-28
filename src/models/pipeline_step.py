"""PipelineStep ORM — single node in the Feature 014 DAG (6 per job)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Enum, ForeignKey, Index, Integer, SmallInteger, String, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.session import Base

if TYPE_CHECKING:
    from src.models.extraction_job import ExtractionJob


class StepType(str, enum.Enum):
    download_video = "download_video"
    pose_analysis = "pose_analysis"
    audio_transcription = "audio_transcription"
    visual_kb_extract = "visual_kb_extract"
    audio_kb_extract = "audio_kb_extract"
    merge_kb = "merge_kb"


class PipelineStepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class PipelineStep(Base):
    __tablename__ = "pipeline_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_type: Mapped[StepType] = mapped_column(
        Enum(StepType, name="pipeline_step_type"), nullable=False
    )
    status: Mapped[PipelineStepStatus] = mapped_column(
        Enum(PipelineStepStatus, name="pipeline_step_status"),
        nullable=False,
        default=PipelineStepStatus.pending,
        server_default=PipelineStepStatus.pending.value,
    )
    retry_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    output_artifact_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationships
    job: Mapped["ExtractionJob"] = relationship("ExtractionJob", back_populates="steps")

    __table_args__ = (
        UniqueConstraint("job_id", "step_type", name="uq_pipeline_steps_job_step"),
        Index(
            "idx_pipeline_steps_running_orphan",
            "started_at",
        ),
    )
