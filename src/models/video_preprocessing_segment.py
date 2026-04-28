"""VideoPreprocessingSegment ORM — Feature-016 per-segment mapping row.

One row per standardised 180s segment (or single row for videos below the
segmentation threshold). Linked to :class:`VideoPreprocessingJob` via
``job_id`` with CASCADE DELETE — see data-model.md §2.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base

if TYPE_CHECKING:
    from src.models.video_preprocessing_job import VideoPreprocessingJob


class VideoPreprocessingSegment(Base):
    __tablename__ = "video_preprocessing_segments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_preprocessing_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cos_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )

    job: Mapped["VideoPreprocessingJob"] = relationship(
        "VideoPreprocessingJob", back_populates="segments"
    )

    __table_args__ = (
        UniqueConstraint("job_id", "segment_index", name="uq_vps_job_index"),
        CheckConstraint("end_ms > start_ms", name="ck_vps_timeline"),
        CheckConstraint("size_bytes > 0", name="ck_vps_size"),
        CheckConstraint("segment_index >= 0", name="ck_vps_index"),
        Index("idx_vps_job_id", "job_id"),
    )
