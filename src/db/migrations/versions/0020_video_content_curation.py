"""Feature-021 — 视频内容清洗与有效片段筛选规范.

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-18

对齐 specs/021-video-content-curation/data-model.md：
1. CREATE TABLE video_curation_jobs（作业级摘要）
2. CREATE TABLE video_curation_segment_results（逐分段判定 + 覆盖留痕；
   含 ``effective_decision`` GENERATED STORED 计算列）
3. ALTER TABLE coach_video_classifications ADD 3 列：
   - last_curation_job_id (UUID NULL FK)
   - low_quality (BOOLEAN NULL)
   - kb_stale_after_override (BOOLEAN NOT NULL DEFAULT FALSE)
4. ALTER TYPE task_type_enum ADD VALUE 'video_curation'
5. INSERT task_channel_configs 默认行（与 0019 同惯例：上线即可热配置）

主键约定：与 ``coach_video_classifications`` / ``video_preprocessing_jobs``
对称使用 PostgreSQL UUID（``gen_random_uuid()``），而非 BIGSERIAL —
这是项目本表族的既有事实标准（Feature-008 / 014 / 016 / 020 一致）。

ENUM 值仅 up 不 down（PostgreSQL 不支持 DROP VALUE FROM ENUM；
章程亦不鼓励破坏性迁移 down）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


# ── 常量 ────────────────────────────────────────────────────────────
_CST_NOW = sa.text("timezone('Asia/Shanghai', now())")


def upgrade() -> None:
    # ── 1. video_curation_jobs 表 + 索引 ─────────────────────────────
    op.create_table(
        "video_curation_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("cos_object_key", sa.String(1024), nullable=False),
        sa.Column(
            "coach_video_classification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("coach_video_classifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "preprocessing_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("video_preprocessing_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("curation_rubric_version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),

        # ── 视频级摘要（success 时落，覆盖时事务内更新） ──────────
        sa.Column("total_segment_count", sa.Integer(), nullable=True),
        sa.Column("accepted_segment_count", sa.Integer(), nullable=True),
        sa.Column("rejected_segment_count", sa.Integer(), nullable=True),
        sa.Column("uncertain_segment_count", sa.Integer(), nullable=True),
        sa.Column("total_duration_seconds", sa.Float(), nullable=True),
        sa.Column("accepted_duration_seconds", sa.Float(), nullable=True),
        sa.Column("accepted_duration_ratio", sa.Float(), nullable=True),
        sa.Column("low_quality", sa.Boolean(), nullable=True),
        sa.Column("audio_unavailable", sa.Boolean(), nullable=True),
        sa.Column("short_video", sa.Boolean(), nullable=True),

        # ── 调度 / 审计 ─────────────────────────────────────────────
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=_CST_NOW,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=False), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=False), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=_CST_NOW,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=_CST_NOW,
        ),

        sa.CheckConstraint(
            "status IN ('pending','running','success','failed')",
            name="ck_curation_job_status",
        ),
        sa.CheckConstraint(
            "accepted_duration_ratio IS NULL "
            "OR (accepted_duration_ratio >= 0 AND accepted_duration_ratio <= 1)",
            name="ck_curation_job_accepted_ratio",
        ),
    )
    op.create_index(
        "ix_curation_jobs_cos_object_key",
        "video_curation_jobs",
        ["cos_object_key"],
    )
    op.create_index(
        "ix_curation_jobs_classification",
        "video_curation_jobs",
        ["coach_video_classification_id"],
    )
    op.create_index(
        "ix_curation_jobs_status_submitted",
        "video_curation_jobs",
        ["status", sa.text("submitted_at DESC")],
    )

    # ── 2. video_curation_segment_results 表 + 索引 ─────────────────
    # effective_decision 用 PostgreSQL GENERATED STORED 计算列实现，
    # 避免应用层每次 query 重算与漏算（data-model.md § 2.2）。
    op.create_table(
        "video_curation_segment_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("video_curation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("segment_start_ms", sa.Integer(), nullable=False),
        sa.Column("segment_end_ms", sa.Integer(), nullable=False),

        # ── 自动决策（不可变） ───────────────────────────────────────
        sa.Column("auto_decision", sa.String(16), nullable=False),
        sa.Column("validity_score", sa.Float(), nullable=False),
        sa.Column("rejection_reason", sa.String(64), nullable=True),
        sa.Column("decision_source", sa.String(16), nullable=False),
        sa.Column("dim_breakdown", postgresql.JSONB(), nullable=True),

        # ── 人工覆盖（同行扩展） ─────────────────────────────────────
        sa.Column("override_decision", sa.String(16), nullable=True),
        sa.Column("override_user", sa.String(64), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("overridden_at", sa.TIMESTAMP(timezone=False), nullable=True),

        # ── 计算列：effective_decision = COALESCE(override_decision, auto_decision)
        sa.Column(
            "effective_decision",
            sa.String(16),
            sa.Computed(
                "COALESCE(override_decision, auto_decision)",
                persisted=True,
            ),
            nullable=False,
        ),

        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=_CST_NOW,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=_CST_NOW,
        ),

        sa.CheckConstraint(
            "auto_decision IN ('accepted','rejected','uncertain')",
            name="ck_curation_seg_auto_decision",
        ),
        sa.CheckConstraint(
            "override_decision IS NULL OR override_decision IN ('accepted','rejected')",
            name="ck_curation_seg_override_decision",
        ),
        sa.CheckConstraint(
            "decision_source IN ('rule','llm')",
            name="ck_curation_seg_decision_source",
        ),
        sa.CheckConstraint(
            "validity_score >= 0 AND validity_score <= 1",
            name="ck_curation_seg_validity_score",
        ),
        sa.UniqueConstraint(
            "job_id",
            "segment_index",
            name="uq_curation_segment",
        ),
    )
    op.create_index(
        "ix_curation_seg_job",
        "video_curation_segment_results",
        ["job_id"],
    )
    op.create_index(
        "ix_curation_seg_effective",
        "video_curation_segment_results",
        ["job_id", "effective_decision"],
    )
    op.create_index(
        "ix_curation_seg_overridden_at",
        "video_curation_segment_results",
        ["overridden_at"],
        postgresql_where=sa.text("overridden_at IS NOT NULL"),
    )

    # ── 3. coach_video_classifications 扩 3 列 ──────────────────────
    op.add_column(
        "coach_video_classifications",
        sa.Column(
            "last_curation_job_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_cvclf_last_curation_job",
        "coach_video_classifications",
        "video_curation_jobs",
        ["last_curation_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "coach_video_classifications",
        sa.Column("low_quality", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "coach_video_classifications",
        sa.Column(
            "kb_stale_after_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_coach_class_last_curation",
        "coach_video_classifications",
        ["last_curation_job_id"],
    )

    # ── 4. ENUM 扩展（仅 up 不 down）─────────────────────────────────
    # PostgreSQL 不支持事务内 ALTER TYPE ... ADD VALUE，使用 raw execute
    # 与 0018 / 0019 一致的写法
    op.execute(
        "ALTER TYPE task_type_enum ADD VALUE IF NOT EXISTS 'video_curation'"
    )

    # ── 5. task_channel_configs 默认行（让通道热配置 API 立刻可见）──
    # 与 0019 一致的 ON CONFLICT DO NOTHING；容量参考 default 通道惯例
    # （清洗任务 I/O 偏轻，单 worker 串行；queue_capacity 比照 KB 抽取打 25%）
    op.execute("""
        INSERT INTO task_channel_configs
            (task_type, queue_capacity, concurrency, enabled, updated_at)
        VALUES
            ('video_curation', 20, 1, true, now())
        ON CONFLICT (task_type) DO NOTHING
    """)


def downgrade() -> None:
    # ── 5. 删 task_channel_configs 默认行 ───────────────────────────
    op.execute(
        "DELETE FROM task_channel_configs WHERE task_type = 'video_curation'"
    )

    # ── 4. ENUM 值不 downgrade（PostgreSQL 不支持 DROP VALUE） ──────
    #   历史行可能已引用 'video_curation'，章程不鼓励破坏性 down。

    # ── 3. coach_video_classifications 反向 ─────────────────────────
    op.drop_index(
        "ix_coach_class_last_curation",
        table_name="coach_video_classifications",
    )
    op.drop_column("coach_video_classifications", "kb_stale_after_override")
    op.drop_column("coach_video_classifications", "low_quality")
    op.drop_constraint(
        "fk_cvclf_last_curation_job",
        "coach_video_classifications",
        type_="foreignkey",
    )
    op.drop_column("coach_video_classifications", "last_curation_job_id")

    # ── 2. video_curation_segment_results 索引 + 表 ─────────────────
    op.drop_index(
        "ix_curation_seg_overridden_at",
        table_name="video_curation_segment_results",
    )
    op.drop_index(
        "ix_curation_seg_effective",
        table_name="video_curation_segment_results",
    )
    op.drop_index(
        "ix_curation_seg_job",
        table_name="video_curation_segment_results",
    )
    op.drop_table("video_curation_segment_results")

    # ── 1. video_curation_jobs 索引 + 表 ────────────────────────────
    op.drop_index(
        "ix_curation_jobs_status_submitted",
        table_name="video_curation_jobs",
    )
    op.drop_index(
        "ix_curation_jobs_classification",
        table_name="video_curation_jobs",
    )
    op.drop_index(
        "ix_curation_jobs_cos_object_key",
        table_name="video_curation_jobs",
    )
    op.drop_table("video_curation_jobs")
