"""ExpertTechPoint ORM model — immutable record of a single technical dimension.

Write-once: once inserted, records are never updated. Knowledge base updates
create new version records instead (append-only versioning).

Unique constraint: (knowledge_base_version, action, dimension)
Validation: param_min ≤ param_ideal ≤ param_max
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
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base


class ExpertTechPoint(Base):
    __tablename__ = "expert_tech_points"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Feature-023: kb_tech_category → kb_action（复合 FK 到 tech_knowledge_bases (action, version)）
    kb_action: Mapped[str] = mapped_column(String(64), nullable=False)
    kb_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Feature-023: 三级分类字段（与 CoachVideoClassification 一致）
    category_l1: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    category_l2: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    category_l3: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Feature 审计修复（迁移 0023）：原 action_type ENUM 列已删除并替换为 varchar
    # + 复合 FK→tech_actions(category_l1, l2, l3, action)，与 V2 字典对齐。
    # 值必须落在 tech_actions 56 行字典内（与 coach_video_classifications 等表同形态）。
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "elbow_angle", "swing_trajectory", "contact_timing", "weight_transfer"
    dimension: Mapped[str] = mapped_column(String(100), nullable=False)
    param_min: Mapped[float] = mapped_column(Float, nullable=False)
    param_max: Mapped[float] = mapped_column(Float, nullable=False)
    param_ideal: Mapped[float] = mapped_column(Float, nullable=False)
    # e.g. "°", "ms", "ratio"
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    # Only points with confidence >= 0.7 are inserted (enforced in tech_extractor)
    extraction_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Feature 002: audio-enhanced extraction fields
    # Source of this tech point: visual / audio / visual+audio
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="visual"
    )
    # FK to the TechSemanticSegment that contributed this point (audio/subtitle source)
    transcript_segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tech_semantic_segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # True when visual and audio sources disagree beyond threshold (param diff > 15%)
    conflict_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # JSONB detail when conflict_flag=True: {"visual": {...}, "audio": {...}, "diff_pct": 0.18}
    conflict_detail: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Feature 审计修复（迁移 0015 / 方案 C2，迁移 0023 把列长度对齐到 64）：
    # 记录提交 KB 提取任务时的 action，与视觉分类器自行判定写入的 action 并存。
    submitted_action: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )

    # Relationships
    knowledge_base: Mapped["TechKnowledgeBase"] = relationship(  # noqa: F821
        "TechKnowledgeBase", back_populates="tech_points"
    )
    source_task: Mapped["AnalysisTask"] = relationship(  # noqa: F821
        "AnalysisTask",
        foreign_keys=[source_video_id],
        back_populates="expert_tech_points",
    )
    transcript_segment: Mapped[Optional["TechSemanticSegment"]] = relationship(  # noqa: F821
        "TechSemanticSegment",
        foreign_keys=[transcript_segment_id],
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["kb_action", "kb_version"],
            ["tech_knowledge_bases.action", "tech_knowledge_bases.version"],
            ondelete="CASCADE",
            name="fk_expert_tech_points_kb",
        ),
        # Feature 审计修复（迁移 0023）：复合 FK 与其他业务表口径一致
        ForeignKeyConstraint(
            ["category_l1", "category_l2", "category_l3", "action"],
            [
                "tech_actions.category_l1",
                "tech_actions.category_l2",
                "tech_actions.category_l3",
                "tech_actions.action",
            ],
            ondelete="RESTRICT",
            onupdate="CASCADE",
            name="fk_expert_tech_points_action",
        ),
        # NOTE: uq_expert_point_kb_action_dim 历史上仅在 ORM 元数据声明，DB 中实际不存在
        # （Feature-019 / Feature-023 均未通过迁移落库该约束）；此处保持与 DB 一致，不再声明
        CheckConstraint(
            "param_min <= param_ideal AND param_ideal <= param_max",
            name="ck_expert_point_param_range",
        ),
        CheckConstraint(
            "extraction_confidence >= 0.0 AND extraction_confidence <= 1.0",
            name="ck_expert_point_confidence_range",
        ),
    )
