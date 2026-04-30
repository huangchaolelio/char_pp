"""ExtractionJob ORM — Feature 014 top-level container for a single KB extraction.

Maps 1:1 to an ``analysis_tasks`` row with ``task_type='kb_extraction'``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base
from src.models.analysis_task import BusinessPhase

if TYPE_CHECKING:
    from src.models.pipeline_step import PipelineStep


class ExtractionJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    cos_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    tech_category: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[ExtractionJobStatus] = mapped_column(
        Enum(ExtractionJobStatus, name="extraction_job_status"),
        nullable=False,
        default=ExtractionJobStatus.pending,
        server_default=ExtractionJobStatus.pending.value,
    )
    worker_hostname: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    enable_audio_analysis: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    audio_language: Mapped[str] = mapped_column(
        String(10), nullable=False, default="zh", server_default="zh"
    )
    force: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    superseded_by_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Feature 018 — Business phase / step mapping (章程原则 X).
    # Fixed TRAINING / extract_kb (data-model.md § 3.2). Auto-populated by hook.
    business_phase: Mapped[BusinessPhase] = mapped_column(
        Enum(BusinessPhase, name="business_phase_enum", create_type=False),
        nullable=False,
    )
    business_step: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    intermediate_cleanup_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )

    # Relationships
    steps: Mapped[list["PipelineStep"]] = relationship(
        "PipelineStep",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="PipelineStep.started_at.nulls_last()",
    )

    __table_args__ = (
        Index(
            "idx_extraction_jobs_status",
            "status",
            "created_at",
        ),
        Index("idx_extraction_jobs_phase", "business_phase"),
    )
