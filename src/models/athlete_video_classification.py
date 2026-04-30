"""AthleteVideoClassification ORM model — 运动员原始视频素材清单（Feature-020）.

与 ``coach_video_classifications`` 在字段结构上对称独立（多 ``athlete_id`` /
少 ``kb_extracted``），但**物理隔离禁止合表**（章程附加约束）。

由 :class:`src.services.cos_athlete_scanner.CosAthleteScanner` 扫描时 upsert；
``preprocessing_job_id`` 由 Feature-016 预处理回写；``last_diagnosis_report_id``
由 :class:`src.services.diagnosis_service.DiagnosisService` 写报告后回写。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    TIMESTAMP,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class AthleteVideoClassification(Base):
    __tablename__ = "athlete_video_classifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cos_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    athlete_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("athletes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    athlete_name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_source: Mapped[str] = mapped_column(String(10), nullable=False)
    tech_category: Mapped[str] = mapped_column(String(50), nullable=False)
    classification_source: Mapped[str] = mapped_column(String(10), nullable=False)
    classification_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    preprocessed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    preprocessing_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_preprocessing_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_diagnosis_report_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("diagnosis_reports.id", ondelete="SET NULL"),
        nullable=True,
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

    __table_args__ = (
        UniqueConstraint("cos_object_key", name="uq_avclf_cos_object_key"),
        CheckConstraint(
            "name_source IN ('map', 'fallback')",
            name="ck_avclf_name_source",
        ),
        CheckConstraint(
            "classification_source IN ('rule', 'llm', 'fallback')",
            name="ck_avclf_classification_source",
        ),
        CheckConstraint(
            "classification_confidence >= 0.0 AND classification_confidence <= 1.0",
            name="ck_avclf_confidence_range",
        ),
        # data-model.md § 3 索引清单
        Index("ix_avclf_athlete_created", "athlete_id", "created_at"),
        Index("ix_avclf_tech_created", "tech_category", "created_at"),
        Index("ix_avclf_preprocessed_tech", "preprocessed", "tech_category"),
    )
