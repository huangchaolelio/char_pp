"""VideoPreprocessingJob ORM — Feature-016 top-level preprocessing task row.

One row per preprocessing attempt against a ``coach_video_classifications``
video. Idempotency is enforced via a partial unique index over
``(cos_object_key) WHERE status='success'``; ``force=true`` submissions mark
the previous success row as ``superseded`` before creating a new ``running``
row — see data-model.md §1 and plan.md (research.md R4).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    Index,
    Integer,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base
from src.models.analysis_task import BusinessPhase

if TYPE_CHECKING:
    from src.models.video_preprocessing_segment import VideoPreprocessingSegment


class PreprocessingJobStatus(str, enum.Enum):
    running = "running"
    success = "success"
    failed = "failed"
    superseded = "superseded"


class VideoPreprocessingJob(Base):
    __tablename__ = "video_preprocessing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cos_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Status values map 1:1 to PreprocessingJobStatus; stored as VARCHAR to allow
    # cheap CHECK-based evolution without Postgres ENUM migrations.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="running"
    )
    force: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )

    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    segment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    original_meta_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    target_standard_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    audio_cos_object_key: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    audio_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    has_audio: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    local_artifact_dir: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )

    # Feature 018 — Business phase / step mapping (章程原则 X).
    # Fixed TRAINING / preprocess_video (data-model.md § 3.3). Auto-populated by hook.
    business_phase: Mapped[BusinessPhase] = mapped_column(
        Enum(BusinessPhase, name="business_phase_enum", create_type=False),
        nullable=False,
    )
    business_step: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
        onupdate=text("timezone('Asia/Shanghai', now())"),
    )

    segments: Mapped[list["VideoPreprocessingSegment"]] = relationship(
        "VideoPreprocessingSegment",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed', 'superseded')",
            name="ck_vpj_status",
        ),
        Index("idx_vpj_status", "status"),
        Index("idx_vpj_cos_object_key", "cos_object_key"),
        Index("idx_vpj_created_at", "created_at"),
        # Partial unique index `uq_vpj_cos_success` is created via raw SQL in
        # the Alembic migration (SQLAlchemy doesn't emit `WHERE` on Index for
        # all dialects uniformly).
    )
