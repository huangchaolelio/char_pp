"""ExpertTechPoint ORM model — immutable record of a single technical dimension.

Write-once: once inserted, records are never updated. Knowledge base updates
create new version records instead (append-only versioning).

Unique constraint: (knowledge_base_version, action_type, dimension)
Validation: param_min ≤ param_ideal ≤ param_max
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
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


class ActionType(str, enum.Enum):
    """动作类型枚举 —— 与 :data:`src.services.tech_classifier.TECH_CATEGORIES` 对齐（21 类）。

    历史上（Feature-002/004）本枚举仅覆盖 12 个视觉分类器可识别的细分动作，
    而 Feature-008/013 的 ``tech_category`` 字段使用 21 类统一标签。两套枚举
    空间不一致导致 ``merge_kb`` 执行时出现 "提交 forehand_attack / 入库 backhand_push"
    的错配。Feature 审计修复（迁移 0015）把两套标签统一到同一空间。

    注：保留原有 12 个旧值（forehand_chop_long / forehand_counter 等）以兼容
    现有视觉分类器 + 测试代码，同时新增 TECH_CATEGORIES 中缺失的 9 项。
    """

    # ── TECH_CATEGORIES 21 类（与 tech_classifier.TECH_CATEGORIES 严格对齐）──
    forehand_push_long = "forehand_push_long"                      # 正手劈长
    forehand_attack = "forehand_attack"                            # 正手攻球
    forehand_topspin = "forehand_topspin"                          # 正手拉球 / 上旋
    forehand_topspin_backspin = "forehand_topspin_backspin"        # 正手拉下旋
    forehand_loop_fast = "forehand_loop_fast"                      # 正手前冲弧圈
    forehand_loop_high = "forehand_loop_high"                      # 正手高调弧圈
    forehand_flick = "forehand_flick"                              # 正手挑打 / 拧拉 / 台内挑打
    backhand_attack = "backhand_attack"                            # 反手攻球
    backhand_topspin = "backhand_topspin"                          # 反手拉球
    backhand_topspin_backspin = "backhand_topspin_backspin"        # 反手拉下旋
    backhand_flick = "backhand_flick"                              # 反手弹击 / 快撕
    backhand_push = "backhand_push"                                # 反手推挡 / 搓球
    serve = "serve"                                                # 发球
    receive = "receive"                                            # 接发球
    footwork = "footwork"                                          # 步法
    forehand_backhand_transition = "forehand_backhand_transition"  # 正反手转换
    defense = "defense"                                            # 防守
    penhold_reverse = "penhold_reverse"                            # 直拍横打
    stance_posture = "stance_posture"                              # 站位 / 姿态
    general = "general"                                            # 综合 / 通用
    unclassified = "unclassified"                                  # 待分类（兜底）

    # ── 视觉分类器兼容细分标签（Feature-002/004 遗留，不在 TECH_CATEGORIES 内）──
    forehand_chop_long = "forehand_chop_long"                      # 正手劈长（细分，兼容保留）
    forehand_counter = "forehand_counter"                          # 正手快带
    forehand_loop_underspin = "forehand_loop_underspin"            # 正手起下旋（细分，兼容保留）
    forehand_position = "forehand_position"                        # 正手跑位 / 两点 / 不定点
    forehand_general = "forehand_general"                          # 正手通用（兜底）
    backhand_general = "backhand_general"                          # 反手通用（兜底）


class ExpertTechPoint(Base):
    __tablename__ = "expert_tech_points"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    knowledge_base_version: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("tech_knowledge_bases.version", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[ActionType] = mapped_column(
        Enum(ActionType, name="action_type_enum"), nullable=False
    )
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

    # Feature 审计修复（迁移 0015 / 方案 C2）：
    # 记录提交 KB 提取任务时的 tech_category（来自 extraction_jobs.tech_category），
    # 与视觉分类器自行判定写入的 action_type 并存。
    # 用途：当两者不一致时可以对账分析分类器偏差，但不阻断落库。
    submitted_tech_category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
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
        UniqueConstraint(
            "knowledge_base_version",
            "action_type",
            "dimension",
            name="uq_expert_point_version_action_dim",
        ),
        CheckConstraint(
            "param_min <= param_ideal AND param_ideal <= param_max",
            name="ck_expert_point_param_range",
        ),
        CheckConstraint(
            "extraction_confidence >= 0.0 AND extraction_confidence <= 1.0",
            name="ck_expert_point_confidence_range",
        ),
    )
