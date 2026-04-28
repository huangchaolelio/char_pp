"""KbConflict ORM — rows staged for human review when visual/audio paths disagree.

One row per (job_id, dimension_name). Feature 014 writes; future review workflow reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Float, ForeignKey, Index, String, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class KbConflict(Base):
    __tablename__ = "kb_conflicts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    cos_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    tech_category: Mapped[str] = mapped_column(String(50), nullable=False)
    dimension_name: Mapped[str] = mapped_column(String(200), nullable=False)
    visual_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    audio_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    visual_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    audio_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    superseded_by_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    resolved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    resolution_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )

    __table_args__ = (
        Index("idx_kb_conflicts_job", "job_id"),
    )
