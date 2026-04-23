"""ReferenceVideoSegment ORM model — one clip within a ReferenceVideo.

Each segment corresponds to a single technical dimension extracted from an
expert source video. Segments are ordered by sequence_order to form the
final assembled reference video.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.session import Base


class ReferenceVideoSegment(Base):
    __tablename__ = "reference_video_segments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reference_video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reference_videos.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Display order within the assembled reference video (0-based or 1-based)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # Technical dimension this segment covers, e.g. "elbow_angle"
    dimension: Mapped[str] = mapped_column(String(100), nullable=False)
    # Human-readable overlay label for this dimension
    label_text: Mapped[str] = mapped_column(Text, nullable=False)
    # COS object key of the source expert video this clip is trimmed from
    source_video_cos_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Clip boundaries within the source video (milliseconds)
    source_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # Confidence that this clip best illustrates the dimension (0.0–1.0)
    extraction_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # True when multiple candidates disagreed above the conflict threshold
    conflict_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Relationships
    reference_video: Mapped["ReferenceVideo"] = relationship(  # noqa: F821
        "ReferenceVideo",
        back_populates="segments",
    )
