"""AudioTranscript ORM model — stores Whisper speech recognition output for a video task."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Enum, Float, ForeignKey, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.db.session import Base


class AudioQualityFlag(str, enum.Enum):
    ok = "ok"
    low_snr = "low_snr"
    unsupported_language = "unsupported_language"
    silent = "silent"


class AudioTranscript(Base):
    """Transcription result produced by Whisper for a single expert video task.

    Each AnalysisTask has at most one AudioTranscript. Sentences are stored as
    JSONB list: [{"start": 1.2, "end": 3.4, "text": "肘部角度保持90度", "confidence": 0.95}, ...]
    """

    __tablename__ = "audio_transcripts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False)          # e.g. "zh"
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)     # e.g. "whisper-small-20231117"
    total_duration_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    snr_db: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quality_flag: Mapped[AudioQualityFlag] = mapped_column(
        Enum(AudioQualityFlag, name="audio_quality_flag_enum"),
        nullable=False,
        default=AudioQualityFlag.ok,
    )
    fallback_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # List of sentence dicts: [{start, end, text, confidence}]
    sentences: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    task: Mapped["AnalysisTask"] = relationship(  # noqa: F821
        "AnalysisTask", back_populates="audio_transcript"
    )
    tech_semantic_segments: Mapped[list["TechSemanticSegment"]] = relationship(  # noqa: F821
        "TechSemanticSegment",
        foreign_keys="TechSemanticSegment.transcript_id",
        back_populates="transcript",
        cascade="all, delete-orphan",
    )
