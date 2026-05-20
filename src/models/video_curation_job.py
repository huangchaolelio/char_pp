"""VideoCurationJob ORM — Feature-021 视频内容清洗作业级摘要.

一条记录代表"对一条视频的一次清洗作业"。一条视频可能有多次清洗（运营带 ``force=true``
重跑、规范升级后重跑），通过 ``cos_object_key + created_at`` 时序对齐。

业务字段语义（详见 specs/021-video-content-curation/data-model.md §2.1）：

- 调度字段：``status / submitted_at / started_at / completed_at`` 标准 lifecycle
- 视频级摘要：``accepted_duration_ratio / low_quality / audio_unavailable / short_video``
  在作业 ``status='success'`` 时一次性派生落库；任何分段被人工覆盖时事务内重算
  并 UPDATE 同行（``services/curation/curation_service.py``）
- 规范版本：``curation_rubric_version`` 是字符串（如 ``"v1"``），由
  ``src/config/curation_rubric/`` 下文件持久化判据快照；按版本号回查 git 还原

关系：

- ``coach_video_classification`` — 多对一，CASCADE DELETE（清洗结果随素材一并清理）
- ``preprocessing_job`` — 多对一，RESTRICT DELETE（避免误删预处理产物丢失依赖锚点）
- ``segment_results`` — 一对多，CASCADE DELETE（与作业生命周期同步）
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    TIMESTAMP,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import text

from src.db.session import Base

if TYPE_CHECKING:
    from src.models.coach_video_classification import CoachVideoClassification
    from src.models.video_curation_segment_result import VideoCurationSegmentResult
    from src.models.video_preprocessing_job import VideoPreprocessingJob


class VideoCurationJob(Base):
    """一次清洗作业；与 ``video_curation_segment_results`` 一对多关联。"""

    __tablename__ = "video_curation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    cos_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    coach_video_classification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coach_video_classifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    preprocessing_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_preprocessing_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    curation_rubric_version: Mapped[str] = mapped_column(String(16), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── 视频级摘要（success / 覆盖时派生） ─────────────────────────
    total_segment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    accepted_segment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rejected_segment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uncertain_segment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    accepted_duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    accepted_duration_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low_quality: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    audio_unavailable: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    short_video: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # ── 调度时间戳 ────────────────────────────────────────────────
    submitted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
        onupdate=text("timezone('Asia/Shanghai', now())"),
    )

    # ── 关系 ─────────────────────────────────────────────────────
    segment_results: Mapped[list["VideoCurationSegmentResult"]] = relationship(
        "VideoCurationSegmentResult",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    coach_video_classification: Mapped["CoachVideoClassification"] = relationship(
        "CoachVideoClassification",
        back_populates="curation_jobs",
        lazy="joined",
        # CoachVideoClassification.last_curation_job_id 也指向本表，避免双向歧义；
        # 显式 foreign_keys 让 SQLAlchemy 解析"哪一侧是关系外键"
        foreign_keys=[coach_video_classification_id],
    )
    preprocessing_job: Mapped["VideoPreprocessingJob"] = relationship(
        "VideoPreprocessingJob",
        lazy="joined",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','success','failed')",
            name="ck_curation_job_status",
        ),
        CheckConstraint(
            "accepted_duration_ratio IS NULL "
            "OR (accepted_duration_ratio >= 0 AND accepted_duration_ratio <= 1)",
            name="ck_curation_job_accepted_ratio",
        ),
        Index("ix_curation_jobs_cos_object_key", "cos_object_key"),
        Index("ix_curation_jobs_classification", "coach_video_classification_id"),
        Index(
            "ix_curation_jobs_status_submitted",
            "status",
            text("submitted_at DESC"),
        ),
    )
