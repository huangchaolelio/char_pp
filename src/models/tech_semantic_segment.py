"""TechSemanticSegment ORM model — a technology-relevant segment identified from audio/subtitle."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class TechSemanticSegment(Base):
    """A segment of audio transcript that contains technical coaching information.

    Produced by KeywordLocator (priority window) and TranscriptTechParser (tech extraction).
    Segments with dimension=None are "reference notes" (verbal descriptions without numeric
    parameters) and are NOT written to the knowledge base.
    """

    __tablename__ = "tech_semantic_segments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audio_transcripts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Time range of the source sentence in the original video (milliseconds)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # Expanded priority window around a keyword hit (None if no keyword triggered this segment)
    priority_window_start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    priority_window_end_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trigger_keyword: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_sentence: Mapped[str] = mapped_column(Text, nullable=False)

    # Extracted technical point (None = reference note only, not written to KB)
    dimension: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    param_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    param_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    param_ideal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    parse_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_reference_note: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )

    # Relationships
    transcript: Mapped["AudioTranscript"] = relationship(  # noqa: F821
        "AudioTranscript", back_populates="tech_semantic_segments"
    )
    task: Mapped["AnalysisTask"] = relationship(  # noqa: F821
        "AnalysisTask", back_populates="tech_semantic_segments"
    )
