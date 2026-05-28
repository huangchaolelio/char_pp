"""Feature-022 — 业务流程四阶段化 + 内容准备阶段引入审核门.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-28

对齐 specs/022-content-review-workflow/data-model.md：
1. ALTER TYPE business_phase_enum ADD VALUE 'CONTENT_PREP'（必须独立事务，详见下方说明）
2. 回填既有任务行的 business_phase（CONTENT_PREP）
3. CREATE TABLE content_review_decisions（审核决策留痕）
4. ALTER TABLE coach_video_classifications ADD 4 列：
   - review_state (String(32) NOT NULL DEFAULT 'pending_review')
   - review_version (Integer NOT NULL DEFAULT 0)
   - last_decision_id (UUID NULL FK → content_review_decisions.id, ON DELETE SET NULL)
   - pending_since (TIMESTAMP NULL)
5. ADD CHECK ck_cvclf_review_state；ADD 4 个新索引（含 1 个 partial index）
6. INSERT task_channel_configs 默认行 ('content_review_gate', 1, 1, true)
   ⚠️ queue_capacity / concurrency 在此 task_type 下无业务意义（审核门是同步 API 决策，
      不入 Celery 队列），但因表上 CHECK 要求 > 0，用 (1, 1) 做最小占位

ENUM 值仅 up 不 down（PostgreSQL 不支持 DROP VALUE FROM ENUM；
章程 v2.2.0 测试阶段亦"只前进"策略）。

⚠️ 关键提示：PostgreSQL 不允许在同一个事务里 ADD VALUE 后立即使用新值。
本迁移使用 Alembic 的 ``autocommit_block()`` 把 ADD VALUE 单独提交，
然后在主事务内做回填与表结构变更。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


# ── 常量 ────────────────────────────────────────────────────────────
_CST_NOW = sa.text("timezone('Asia/Shanghai', now())")


def upgrade() -> None:
    # ── Step 1: ALTER TYPE 两个 enum 增值（独立事务） ────────────────
    # PostgreSQL 不允许在同一事务里 ADD VALUE 后立即使用新值；
    # 用 autocommit_block 让本组语句独立提交。两个 enum 一起处理：
    # - business_phase_enum: 增 CONTENT_PREP（Step 2 立即用于回填）
    # - task_type_enum: 增 content_review_gate（Step 6 立即用于 INSERT）
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE business_phase_enum "
            "ADD VALUE IF NOT EXISTS 'CONTENT_PREP' BEFORE 'TRAINING'"
        )
        op.execute(
            "ALTER TYPE task_type_enum "
            "ADD VALUE IF NOT EXISTS 'content_review_gate'"
        )

    # ── Step 2: 回填既有任务行 business_phase = 'CONTENT_PREP' ────────
    # 内容准备阶段包含：scan_cos_videos / preprocess_video / classify_video / curate_segments
    op.execute(
        """
        UPDATE analysis_tasks
        SET business_phase = 'CONTENT_PREP'
        WHERE business_step IN (
            'scan_cos_videos',
            'preprocess_video',
            'classify_video',
            'curate_segments'
        )
        """
    )
    # 视频预处理作业表统一归 CONTENT_PREP（其全部行均为 preprocess_video 步骤）
    op.execute(
        "UPDATE video_preprocessing_jobs "
        "SET business_phase = 'CONTENT_PREP' "
        "WHERE business_phase IS NOT NULL"
    )
    # video_curation_jobs 若已有 business_phase 列则同步回填（条件判断：
    # Feature-021 迁移 0020 未在 video_curation_jobs 加 phase 列；这里做防御性写法）
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'video_curation_jobs'
                  AND column_name = 'business_phase'
            ) THEN
                EXECUTE 'UPDATE video_curation_jobs SET business_phase = ''CONTENT_PREP''';
            END IF;
        END$$;
        """
    )

    # ── Step 3: CREATE TABLE content_review_decisions ─────────────────
    op.create_table(
        "content_review_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "cvclf_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "coach_video_classifications.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "cleansing_version",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "video_curation_jobs.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewer_id", sa.String(64), nullable=False),
        sa.Column(
            "decided_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=_CST_NOW,
        ),
        sa.Column(
            "superseded_at",
            sa.TIMESTAMP(timezone=False),
            nullable=True,
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_crd_decision",
        ),
        sa.CheckConstraint(
            "(decision = 'rejected' AND reason_code IS NOT NULL) "
            "OR decision = 'approved'",
            name="ck_crd_rejected_requires_reason",
        ),
    )
    op.create_index(
        "idx_crd_cvclf_decided",
        "content_review_decisions",
        ["cvclf_id", "decided_at"],
    )
    op.create_index(
        "idx_crd_decided_at",
        "content_review_decisions",
        ["decided_at"],
    )
    op.create_index(
        "idx_crd_reviewer_decided",
        "content_review_decisions",
        ["reviewer_id", "decided_at"],
    )

    # ── Step 4: coach_video_classifications 加 4 列 ───────────────────
    op.add_column(
        "coach_video_classifications",
        sa.Column(
            "review_state",
            sa.String(32),
            nullable=False,
            server_default="pending_review",
        ),
    )
    op.add_column(
        "coach_video_classifications",
        sa.Column(
            "review_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "coach_video_classifications",
        sa.Column(
            "last_decision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_cvclf_last_decision",
        "coach_video_classifications",
        "content_review_decisions",
        ["last_decision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "coach_video_classifications",
        sa.Column(
            "pending_since",
            sa.TIMESTAMP(timezone=False),
            nullable=True,
        ),
    )

    # ── Step 5: 索引 + CHECK ──────────────────────────────────────────
    op.create_check_constraint(
        "ck_cvclf_review_state",
        "coach_video_classifications",
        "review_state IN ('pending_review', 'approved', 'rejected', 'stale')",
    )
    op.create_index(
        "idx_cvclf_review_state_decided",
        "coach_video_classifications",
        ["review_state", "last_decision_id"],
    )
    op.create_index(
        "idx_cvclf_coach_review",
        "coach_video_classifications",
        ["coach_name", "review_state"],
    )
    op.create_index(
        "idx_cvclf_tech_review",
        "coach_video_classifications",
        ["tech_category", "review_state"],
    )
    # 部分索引：仅索引 pending 行；用于积压告警 + 平均等待时延扫描
    op.create_index(
        "idx_cvclf_pending_since",
        "coach_video_classifications",
        ["pending_since"],
        postgresql_where=sa.text("review_state = 'pending_review'"),
    )

    # ── Step 6: task_channel_configs 默认行 ───────────────────────────
    # enabled=true 表"严格审核门"（默认）；enabled=false 表"绕过模式"
    # ⚠️ queue_capacity / concurrency 在此 task_type 下**无业务意义**：审核门
    # 是同步 API 决策，不入 Celery 队列，但 task_channel_configs 的两个 CHECK
    # 约束要求 (queue_capacity > 0 AND concurrency > 0)，故用 (1, 1) 做最小占位。
    # evaluate_review_gate 仅读 task_type='content_review_gate' 的 enabled 字段。
    op.execute(
        """
        INSERT INTO task_channel_configs
            (task_type, queue_capacity, concurrency, enabled, updated_at)
        VALUES
            ('content_review_gate', 1, 1, true, timezone('Asia/Shanghai', now()))
        ON CONFLICT (task_type) DO NOTHING
        """
    )


def downgrade() -> None:
    # ── Step 6: 删配置行 ──────────────────────────────────────────────
    op.execute(
        "DELETE FROM task_channel_configs "
        "WHERE task_type = 'content_review_gate'"
    )

    # ── Step 5: 删 4 个索引 + CHECK ───────────────────────────────────
    op.drop_index(
        "idx_cvclf_pending_since",
        table_name="coach_video_classifications",
    )
    op.drop_index(
        "idx_cvclf_tech_review",
        table_name="coach_video_classifications",
    )
    op.drop_index(
        "idx_cvclf_coach_review",
        table_name="coach_video_classifications",
    )
    op.drop_index(
        "idx_cvclf_review_state_decided",
        table_name="coach_video_classifications",
    )
    op.drop_constraint(
        "ck_cvclf_review_state",
        "coach_video_classifications",
        type_="check",
    )

    # ── Step 4: 删 4 列（先反 FK） ────────────────────────────────────
    op.drop_constraint(
        "fk_cvclf_last_decision",
        "coach_video_classifications",
        type_="foreignkey",
    )
    op.drop_column("coach_video_classifications", "pending_since")
    op.drop_column("coach_video_classifications", "last_decision_id")
    op.drop_column("coach_video_classifications", "review_version")
    op.drop_column("coach_video_classifications", "review_state")

    # ── Step 3: 删 content_review_decisions 表 + 索引 ─────────────────
    op.drop_index(
        "idx_crd_reviewer_decided",
        table_name="content_review_decisions",
    )
    op.drop_index(
        "idx_crd_decided_at",
        table_name="content_review_decisions",
    )
    op.drop_index(
        "idx_crd_cvclf_decided",
        table_name="content_review_decisions",
    )
    op.drop_table("content_review_decisions")

    # ── Step 2: 回填不反向（CONTENT_PREP 行已是正确语义） ─────────────
    # 测试阶段"只前进"策略；强行回退会破坏阶段语义。

    # ── Step 1: ENUM 不回退（PostgreSQL 不支持 DROP VALUE） ───────────
    # 章程 v2.2.0 测试阶段允许；生产环境如需回退须人工重建 enum type。
