"""CoachVideoClassification ORM model — tech classification record for a coach video.

Each record represents one .mp4 file under COS_VIDEO_ALL_COCAH path,
with its detected tech_category and metadata.

classification_source lifecycle:
  - 'rule': keyword-matched classification (confidence=1.0)
  - 'llm': LLM-inferred classification (confidence from LLM)
  - 'manual': human-corrected via PATCH API (confidence=1.0, irreversible)
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, TEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base

if TYPE_CHECKING:
    from src.models.video_curation_job import VideoCurationJob


class CoachVideoClassification(Base):
    __tablename__ = "coach_video_classifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    coach_name: Mapped[str] = mapped_column(String(100), nullable=False)
    course_series: Mapped[str] = mapped_column(String(255), nullable=False)
    cos_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    tech_category: Mapped[str] = mapped_column(String(64), nullable=False)
    tech_tags: Mapped[list[str]] = mapped_column(
        ARRAY(TEXT()), nullable=False, server_default="{}"
    )
    raw_tech_desc: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    classification_source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="rule"
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    duration_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    name_source: Mapped[str] = mapped_column(String(10), nullable=False, default="map")
    kb_extracted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Feature-016: set to true when at least one VideoPreprocessingJob with
    # status='success' exists for this cos_object_key. Kept independent of
    # kb_extracted so ops can query "preprocessed but not yet KB-extracted".
    preprocessed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # ── Feature-021: 视频内容清洗扩列 ────────────────────────────
    # 由 services/curation/curation_service.py 在清洗 success 后维护：
    # - last_curation_job_id: 指向最近一次成功清洗作业（FK，SET NULL on delete）
    # - low_quality:           从 video_curation_jobs.low_quality 同步，避免列表 join
    # - kb_stale_after_override: 任意分段在 KB 抽取作业完成后被覆盖时为 true；
    #     运营 POST /extraction-jobs/{id}/rerun 重抽完成后清零
    last_curation_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_curation_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    low_quality: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    kb_stale_after_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # ── Feature-022: 内容审核状态机字段 ────────────────────────────
    # 由 services/content_review/* 在审核决策提交 / 重新清洗后维护：
    # - review_state:          四态枚举（pending_review / approved / rejected / stale）
    #   * pending_review: 等待审核（清洗成功后默认 / 重新清洗后从 approved 迁回）
    #   * approved:       审核通过（KB 抽取门放行）
    #   * rejected:       审核拒绝（澄清 Q5：永久保留，不再迁出此态）
    #   * stale:          内部中间态，approved 重洗后理论上经过它再到 pending_review
    #     —— 但 stale_handler 直接合并迁移，不暴露给客户端
    # - review_version:        乐观锁版本号；每次状态变更 +1，EP-3 决策提交时校验
    # - pending_since:          首次进入 pending_review 的时刻；用于积压告警 / SLA 统计
    #   决策落地（approved / rejected）时清空（NULL）
    # - last_decision_id:      指向最近一次决策行（审计回溯）；FK SET NULL on delete
    review_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending_review",
        server_default="pending_review",
    )
    review_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    pending_since: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False), nullable=True,
    )
    last_decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_review_decisions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=text("timezone('Asia/Shanghai', now())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
        onupdate=text("timezone('Asia/Shanghai', now())"),
    )

    # ── 关系 ─────────────────────────────────────────────────
    # 一对多：本素材的所有清洗作业（按时序留痕，不仅是 latest）
    curation_jobs: Mapped[list["VideoCurationJob"]] = relationship(
        "VideoCurationJob",
        back_populates="coach_video_classification",
        cascade="all, delete-orphan",
        lazy="noload",
        # 与 last_curation_job_id 反向 FK 区分；显式指定关系外键避免歧义
        foreign_keys="VideoCurationJob.coach_video_classification_id",
    )

    __table_args__ = (
        UniqueConstraint("cos_object_key", name="uq_cvclf_cos_object_key"),
        CheckConstraint(
            "classification_source IN ('rule', 'llm', 'manual')",
            name="ck_cvclf_source",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_cvclf_confidence_range",
        ),
        CheckConstraint(
            "name_source IN ('map', 'fallback')",
            name="ck_cvclf_name_source",
        ),
        # Feature-022: review_state 四态闭包
        CheckConstraint(
            "review_state IN ('pending_review', 'approved', 'rejected', 'stale')",
            name="ck_cvclf_review_state",
        ),
        Index("idx_cvclf_coach", "coach_name"),
        Index("idx_cvclf_tech", "tech_category"),
        Index("idx_cvclf_kb", "kb_extracted"),
        Index("idx_cvclf_preprocessed", "preprocessed"),
        Index("idx_cvclf_coach_tech", "coach_name", "tech_category"),
        Index("ix_coach_class_last_curation", "last_curation_job_id"),
        # Feature-022 索引：审核工作台 P95 < 500ms（澄清 Q4 中规模 50–200 条/日）
        # idx_cvclf_review_state_pending_since: 列表默认按"积压最久"排序；
        #   审核工作台主查询路径（state=pending_review ORDER BY pending_since ASC）
        Index(
            "idx_cvclf_review_state_pending_since",
            "review_state", "pending_since",
        ),
        # idx_cvclf_review_state_tech: 工作台二级筛选（state + tech_category）
        Index(
            "idx_cvclf_review_state_tech",
            "review_state", "tech_category",
        ),
        # idx_cvclf_review_state_coach: 工作台三级筛选（state + coach_name）
        Index(
            "idx_cvclf_review_state_coach",
            "review_state", "coach_name",
        ),
        # idx_cvclf_last_decision: 详情接口快速 join 决策表
        Index("idx_cvclf_last_decision", "last_decision_id"),
    )
