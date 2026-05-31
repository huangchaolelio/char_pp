"""Feature-023 — 技术分类体系重构：21 类 tech_category → 严格四级 + 字典约束.

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-31

权威参考: specs/023-tech-classification-rebuild/data-model.md § 3

零兼容、不保留 aliases、不保留 classifier_version 切换路径。
迁移本身仅做 schema 重建：DROP 旧 tech_category 列与索引、CREATE tech_actions 字典 + seed 56 行、
ADD 4 级字段 + 复合 FK、把 tech_knowledge_bases 复合 PK 从 (tech_category, version) 重命名为 (action, version)、
4 张子表外键列 kb_tech_category → kb_action。

⚠️ 业务数据清场移交 system-init skill：迁移本身不做 TRUNCATE。
   执行顺序：停 worker → alembic downgrade <prev> → alembic upgrade head
            → system-init skill TRUNCATE + reseed task_channel_configs
            → 启动 worker → 触发全量 COS 扫描重建数据。

⚠️ 前置条件: 业务表必须为空（system-init 已执行）。否则 RENAME 列 / 改 PK 失败。
"""

from __future__ import annotations

import csv
from pathlib import Path

import sqlalchemy as sa
from alembic import op


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


# ── 常量 ────────────────────────────────────────────────────────────
_CST_NOW = sa.text("timezone('Asia/Shanghai', now())")

# 字典 seed 来源：spec contracts 目录下的清洗版 CSV（56 行 + 表头）
# 路径相对仓库根；alembic upgrade 由仓库根运行，路径稳定
_SEED_CSV = Path("specs/023-tech-classification-rebuild/contracts/tech-actions-seed.csv")

# 8 个业务表的 (l1, l2, l3) 三级字段 NULLABLE（仅 unclassified 时全 NULL）
_NULLABLE_3L_TABLES = (
    "coach_video_classifications",
    "video_classifications",
    "expert_tech_points",
    "tech_knowledge_bases",
    "tech_standards",
    "teaching_tips",
    "diagnosis_reports",
)


def _strip_zwsp(s: str) -> str:
    """去除 U+200B 零宽字符 + trim 普通空白."""
    return s.replace("\u200b", "").strip()


def _load_seed_rows() -> list[dict[str, str]]:
    """读取 contracts/tech-actions-seed.csv 并清洗，返回 56 行字典."""
    rows: list[dict[str, str]] = []
    with _SEED_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = {k: _strip_zwsp(v) for k, v in raw.items()}
            if not row.get("action"):
                continue  # 防御：跳过空行
            rows.append(row)
    if len(rows) != 56:
        raise RuntimeError(
            f"tech-actions-seed.csv 期望 56 行，实际 {len(rows)} 行。"
            "检查 CSV 内容是否被改动。"
        )
    return rows


def upgrade() -> None:
    # ── Step 1: CREATE tech_actions 字典表 ────────────────────────────
    op.create_table(
        "tech_actions",
        sa.Column("category_l1", sa.String(32), nullable=False),
        sa.Column("category_l2", sa.String(32), nullable=False),
        sa.Column("category_l3", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=_CST_NOW,
        ),
        sa.PrimaryKeyConstraint(
            "category_l1",
            "category_l2",
            "category_l3",
            "action",
            name="pk_tech_actions",
        ),
    )
    op.create_index(
        "ix_tech_actions_l1l2l3",
        "tech_actions",
        ["category_l1", "category_l2", "category_l3"],
    )

    # ── Step 2: seed 56 行 ────────────────────────────────────────────
    bind = op.get_bind()
    seed_rows = _load_seed_rows()
    bind.execute(
        sa.text(
            "INSERT INTO tech_actions (category_l1, category_l2, category_l3, action) "
            "VALUES (:category_l1, :category_l2, :category_l3, :action)"
        ),
        seed_rows,
    )

    # ── Step 3: DROP 业务表外键到 tech_knowledge_bases (5 张子表) ─────
    # 必须在改 PK 之前先 DROP 所有引用它的 FK
    op.drop_constraint(
        "fk_expert_tech_points_kb",
        "expert_tech_points",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_analysis_tasks_kb",
        "analysis_tasks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_teaching_tips_kb",
        "teaching_tips",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_reference_videos_kb",
        "reference_videos",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_skill_executions_kb",
        "skill_executions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_athlete_motion_analyses_kb",
        "athlete_motion_analyses",
        type_="foreignkey",
    )

    # ── Step 4: coach_video_classifications：DROP tech_category 列 + 索引 ──
    # 先 DROP 引用 tech_category 的 4 个索引
    op.drop_index(
        "idx_cvclf_tech",
        table_name="coach_video_classifications",
    )
    op.drop_index(
        "idx_cvclf_coach_tech",
        table_name="coach_video_classifications",
    )
    op.drop_index(
        "idx_cvclf_review_state_tech",
        table_name="coach_video_classifications",
    )
    op.drop_column("coach_video_classifications", "tech_category")
    op.add_column(
        "coach_video_classifications",
        sa.Column("category_l1", sa.String(32), nullable=True),
    )
    op.add_column(
        "coach_video_classifications",
        sa.Column("category_l2", sa.String(32), nullable=True),
    )
    op.add_column(
        "coach_video_classifications",
        sa.Column("category_l3", sa.String(64), nullable=True),
    )
    op.add_column(
        "coach_video_classifications",
        sa.Column("action", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_cvclf_action",
        "coach_video_classifications",
        "tech_actions",
        ["category_l1", "category_l2", "category_l3", "action"],
        ["category_l1", "category_l2", "category_l3", "action"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_cvclf_action",
        "coach_video_classifications",
        ["action"],
    )
    op.create_index(
        "idx_cvclf_review_state_action",
        "coach_video_classifications",
        ["review_state", "action"],
    )
    op.create_index(
        "idx_cvclf_coach_action",
        "coach_video_classifications",
        ["coach_name", "action"],
    )

    # ── Step 5: video_classifications：DROP tech_category 列 + 添加 4 级 ──
    # video_classifications 旧表用单列 cos_object_key 做 PK，tech_category 是普通列
    op.drop_column("video_classifications", "tech_category")
    op.drop_column("video_classifications", "tech_sub_category")
    op.drop_column("video_classifications", "tech_detail")
    op.add_column(
        "video_classifications",
        sa.Column("category_l1", sa.String(32), nullable=True),
    )
    op.add_column(
        "video_classifications",
        sa.Column("category_l2", sa.String(32), nullable=True),
    )
    op.add_column(
        "video_classifications",
        sa.Column("category_l3", sa.String(64), nullable=True),
    )
    op.add_column(
        "video_classifications",
        sa.Column("action", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_vclf_action",
        "video_classifications",
        "tech_actions",
        ["category_l1", "category_l2", "category_l3", "action"],
        ["category_l1", "category_l2", "category_l3", "action"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    # ── Step 6: tech_knowledge_bases：复合 PK 重命名 + 列改造 ─────────
    # DROP 旧 PK
    op.drop_constraint(
        "pk_tech_kb_cat_ver",
        "tech_knowledge_bases",
        type_="primary",
    )
    # 旧 partial unique index uq_tech_kb_active_per_category
    op.execute("DROP INDEX IF EXISTS uq_tech_kb_active_per_category")
    # RENAME 列
    op.alter_column(
        "tech_knowledge_bases",
        "tech_category",
        new_column_name="action",
    )
    # ADD 三级字段
    op.add_column(
        "tech_knowledge_bases",
        sa.Column("category_l1", sa.String(32), nullable=True),
    )
    op.add_column(
        "tech_knowledge_bases",
        sa.Column("category_l2", sa.String(32), nullable=True),
    )
    op.add_column(
        "tech_knowledge_bases",
        sa.Column("category_l3", sa.String(64), nullable=True),
    )
    # 重建复合 PK
    op.create_primary_key(
        "pk_tech_kb_action_ver",
        "tech_knowledge_bases",
        ["action", "version"],
    )
    # 字典外键
    op.create_foreign_key(
        "fk_tkb_action",
        "tech_knowledge_bases",
        "tech_actions",
        ["category_l1", "category_l2", "category_l3", "action"],
        ["category_l1", "category_l2", "category_l3", "action"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )
    # 新 partial unique index per-action
    op.execute(
        "CREATE UNIQUE INDEX uq_tech_kb_active_per_action "
        "ON tech_knowledge_bases (action) WHERE status = 'active'"
    )

    # ── Step 7: expert_tech_points：rename 2 个列 + ADD 三级 ──────────
    op.alter_column(
        "expert_tech_points",
        "submitted_tech_category",
        new_column_name="submitted_action",
    )
    op.alter_column(
        "expert_tech_points",
        "kb_tech_category",
        new_column_name="kb_action",
    )
    op.add_column(
        "expert_tech_points",
        sa.Column("category_l1", sa.String(32), nullable=True),
    )
    op.add_column(
        "expert_tech_points",
        sa.Column("category_l2", sa.String(32), nullable=True),
    )
    op.add_column(
        "expert_tech_points",
        sa.Column("category_l3", sa.String(64), nullable=True),
    )
    # 旧 unique 约束 uq_expert_point_kb_action_dim 仅在 ORM 元数据声明，DB 中实际不存在
    # （历史遗留：先前迁移未创建该约束）；本迁移不重建该约束，保留 ORM/DB 一致性。
    # 重建到 tech_knowledge_bases 的复合 FK（PK 已改名为 (action, version)）
    op.create_foreign_key(
        "fk_expert_tech_points_kb",
        "expert_tech_points",
        "tech_knowledge_bases",
        ["kb_action", "kb_version"],
        ["action", "version"],
        ondelete="CASCADE",
    )
    # expert_tech_points 没有独立的 action 列（action_type 是 ENUM 独立存在）
    # 因此本表不引入到 tech_actions 字典的复合 FK；只保留对 KB 的复合 FK

    # ── Step 8: tech_standards：rename + ADD 三级 ──────────────────────
    op.alter_column(
        "tech_standards",
        "tech_category",
        new_column_name="action",
    )
    op.add_column(
        "tech_standards",
        sa.Column("category_l1", sa.String(32), nullable=True),
    )
    op.add_column(
        "tech_standards",
        sa.Column("category_l2", sa.String(32), nullable=True),
    )
    op.add_column(
        "tech_standards",
        sa.Column("category_l3", sa.String(64), nullable=True),
    )
    op.drop_constraint(
        "uq_ts_tech_version",
        "tech_standards",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ts_action_version",
        "tech_standards",
        ["action", "version"],
    )
    op.execute("DROP INDEX IF EXISTS idx_ts_active_per_category")
    op.execute(
        "CREATE UNIQUE INDEX idx_ts_active_per_action "
        "ON tech_standards (action, source_fingerprint) WHERE status = 'active'"
    )
    op.create_foreign_key(
        "fk_ts_action",
        "tech_standards",
        "tech_actions",
        ["category_l1", "category_l2", "category_l3", "action"],
        ["category_l1", "category_l2", "category_l3", "action"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    # ── Step 9: teaching_tips：rename 2 个列 + ADD 三级 ────────────────
    op.alter_column(
        "teaching_tips",
        "tech_category",
        new_column_name="action",
    )
    op.alter_column(
        "teaching_tips",
        "kb_tech_category",
        new_column_name="kb_action",
    )
    op.add_column(
        "teaching_tips",
        sa.Column("category_l1", sa.String(32), nullable=True),
    )
    op.add_column(
        "teaching_tips",
        sa.Column("category_l2", sa.String(32), nullable=True),
    )
    op.add_column(
        "teaching_tips",
        sa.Column("category_l3", sa.String(64), nullable=True),
    )
    op.drop_index("ix_teaching_tips_tech_category", table_name="teaching_tips")
    op.create_index("ix_teaching_tips_action", "teaching_tips", ["action"])
    op.drop_index("ix_teaching_tips_kb", table_name="teaching_tips")
    op.create_index(
        "ix_teaching_tips_kb",
        "teaching_tips",
        ["kb_action", "kb_version"],
    )
    op.create_foreign_key(
        "fk_teaching_tips_kb",
        "teaching_tips",
        "tech_knowledge_bases",
        ["kb_action", "kb_version"],
        ["action", "version"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_tt_action",
        "teaching_tips",
        "tech_actions",
        ["category_l1", "category_l2", "category_l3", "action"],
        ["category_l1", "category_l2", "category_l3", "action"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    # ── Step 10: diagnosis_reports：rename + ADD 三级 ──────────────────
    op.alter_column(
        "diagnosis_reports",
        "tech_category",
        new_column_name="action",
    )
    op.add_column(
        "diagnosis_reports",
        sa.Column("category_l1", sa.String(32), nullable=True),
    )
    op.add_column(
        "diagnosis_reports",
        sa.Column("category_l2", sa.String(32), nullable=True),
    )
    op.add_column(
        "diagnosis_reports",
        sa.Column("category_l3", sa.String(64), nullable=True),
    )
    # 没有 idx_dr_tech_category 索引（按现有 schema）；如果存在则改名
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_dr_tech_category') "
        "THEN ALTER INDEX idx_dr_tech_category RENAME TO idx_dr_action; END IF; "
        "END $$;"
    )
    op.create_foreign_key(
        "fk_dr_action",
        "diagnosis_reports",
        "tech_actions",
        ["category_l1", "category_l2", "category_l3", "action"],
        ["category_l1", "category_l2", "category_l3", "action"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )

    # ── Step 11: analysis_tasks：rename kb_tech_category → kb_action ──
    op.alter_column(
        "analysis_tasks",
        "kb_tech_category",
        new_column_name="kb_action",
    )
    op.create_foreign_key(
        "fk_analysis_tasks_kb",
        "analysis_tasks",
        "tech_knowledge_bases",
        ["kb_action", "kb_version"],
        ["action", "version"],
        ondelete="SET NULL",
    )

    # ── Step 12: reference_videos：rename kb_tech_category → kb_action ──
    op.alter_column(
        "reference_videos",
        "kb_tech_category",
        new_column_name="kb_action",
    )
    op.create_foreign_key(
        "fk_reference_videos_kb",
        "reference_videos",
        "tech_knowledge_bases",
        ["kb_action", "kb_version"],
        ["action", "version"],
        ondelete="RESTRICT",
    )

    # ── Step 13: skill_executions：rename kb_tech_category → kb_action ──
    op.alter_column(
        "skill_executions",
        "kb_tech_category",
        new_column_name="kb_action",
    )
    op.create_foreign_key(
        "fk_skill_executions_kb",
        "skill_executions",
        "tech_knowledge_bases",
        ["kb_action", "kb_version"],
        ["action", "version"],
        ondelete="SET NULL",
    )

    # ── Step 14: athlete_motion_analyses：rename kb_tech_category → kb_action ──
    op.alter_column(
        "athlete_motion_analyses",
        "kb_tech_category",
        new_column_name="kb_action",
    )
    op.create_foreign_key(
        "fk_athlete_motion_analyses_kb",
        "athlete_motion_analyses",
        "tech_knowledge_bases",
        ["kb_action", "kb_version"],
        ["action", "version"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """对称重建 tech_category 列结构、删除 tech_actions 字典与四级列、恢复旧 PK.

    ⚠️ 业务数据不可回填（已被 system-init TRUNCATE）。downgrade 仅恢复 schema 形态。
    """

    # ── Step 14 -> reverse: athlete_motion_analyses ───────────────────
    op.drop_constraint(
        "fk_athlete_motion_analyses_kb",
        "athlete_motion_analyses",
        type_="foreignkey",
    )
    op.alter_column(
        "athlete_motion_analyses",
        "kb_action",
        new_column_name="kb_tech_category",
    )

    # ── Step 13 -> reverse: skill_executions ──────────────────────────
    op.drop_constraint(
        "fk_skill_executions_kb",
        "skill_executions",
        type_="foreignkey",
    )
    op.alter_column(
        "skill_executions",
        "kb_action",
        new_column_name="kb_tech_category",
    )

    # ── Step 12 -> reverse: reference_videos ──────────────────────────
    op.drop_constraint(
        "fk_reference_videos_kb",
        "reference_videos",
        type_="foreignkey",
    )
    op.alter_column(
        "reference_videos",
        "kb_action",
        new_column_name="kb_tech_category",
    )

    # ── Step 11 -> reverse: analysis_tasks ────────────────────────────
    op.drop_constraint(
        "fk_analysis_tasks_kb",
        "analysis_tasks",
        type_="foreignkey",
    )
    op.alter_column(
        "analysis_tasks",
        "kb_action",
        new_column_name="kb_tech_category",
    )

    # ── Step 10 -> reverse: diagnosis_reports ─────────────────────────
    op.drop_constraint(
        "fk_dr_action",
        "diagnosis_reports",
        type_="foreignkey",
    )
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_dr_action') "
        "THEN ALTER INDEX idx_dr_action RENAME TO idx_dr_tech_category; END IF; "
        "END $$;"
    )
    op.drop_column("diagnosis_reports", "category_l3")
    op.drop_column("diagnosis_reports", "category_l2")
    op.drop_column("diagnosis_reports", "category_l1")
    op.alter_column(
        "diagnosis_reports",
        "action",
        new_column_name="tech_category",
    )

    # ── Step 9 -> reverse: teaching_tips ──────────────────────────────
    op.drop_constraint("fk_tt_action", "teaching_tips", type_="foreignkey")
    op.drop_constraint("fk_teaching_tips_kb", "teaching_tips", type_="foreignkey")
    op.drop_index("ix_teaching_tips_kb", table_name="teaching_tips")
    op.drop_index("ix_teaching_tips_action", table_name="teaching_tips")
    op.drop_column("teaching_tips", "category_l3")
    op.drop_column("teaching_tips", "category_l2")
    op.drop_column("teaching_tips", "category_l1")
    op.alter_column(
        "teaching_tips",
        "kb_action",
        new_column_name="kb_tech_category",
    )
    op.alter_column(
        "teaching_tips",
        "action",
        new_column_name="tech_category",
    )
    # 重建旧索引和 FK
    op.create_index(
        "ix_teaching_tips_tech_category",
        "teaching_tips",
        ["tech_category"],
    )
    op.create_index(
        "ix_teaching_tips_kb",
        "teaching_tips",
        ["kb_tech_category", "kb_version"],
    )
    op.create_foreign_key(
        "fk_teaching_tips_kb",
        "teaching_tips",
        "tech_knowledge_bases",
        ["kb_tech_category", "kb_version"],
        ["tech_category", "version"],
        ondelete="CASCADE",
    )

    # ── Step 8 -> reverse: tech_standards ─────────────────────────────
    op.drop_constraint("fk_ts_action", "tech_standards", type_="foreignkey")
    op.execute("DROP INDEX IF EXISTS idx_ts_active_per_action")
    op.drop_constraint(
        "uq_ts_action_version",
        "tech_standards",
        type_="unique",
    )
    op.drop_column("tech_standards", "category_l3")
    op.drop_column("tech_standards", "category_l2")
    op.drop_column("tech_standards", "category_l1")
    op.alter_column(
        "tech_standards",
        "action",
        new_column_name="tech_category",
    )
    op.create_unique_constraint(
        "uq_ts_tech_version",
        "tech_standards",
        ["tech_category", "version"],
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_ts_active_per_category "
        "ON tech_standards (tech_category, source_fingerprint) WHERE status = 'active'"
    )

    # ── Step 7 -> reverse: expert_tech_points ─────────────────────────
    op.drop_constraint(
        "fk_expert_tech_points_kb",
        "expert_tech_points",
        type_="foreignkey",
    )
    op.drop_column("expert_tech_points", "category_l3")
    op.drop_column("expert_tech_points", "category_l2")
    op.drop_column("expert_tech_points", "category_l1")
    op.alter_column(
        "expert_tech_points",
        "kb_action",
        new_column_name="kb_tech_category",
    )
    op.alter_column(
        "expert_tech_points",
        "submitted_action",
        new_column_name="submitted_tech_category",
    )

    # ── Step 6 -> reverse: tech_knowledge_bases ────────────────────────
    op.drop_constraint(
        "fk_tkb_action",
        "tech_knowledge_bases",
        type_="foreignkey",
    )
    op.execute("DROP INDEX IF EXISTS uq_tech_kb_active_per_action")
    op.drop_constraint(
        "pk_tech_kb_action_ver",
        "tech_knowledge_bases",
        type_="primary",
    )
    op.drop_column("tech_knowledge_bases", "category_l3")
    op.drop_column("tech_knowledge_bases", "category_l2")
    op.drop_column("tech_knowledge_bases", "category_l1")
    op.alter_column(
        "tech_knowledge_bases",
        "action",
        new_column_name="tech_category",
    )
    op.create_primary_key(
        "pk_tech_kb_cat_ver",
        "tech_knowledge_bases",
        ["tech_category", "version"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_tech_kb_active_per_category "
        "ON tech_knowledge_bases (tech_category) WHERE status = 'active'"
    )

    # ── Step 5 -> reverse: video_classifications ──────────────────────
    op.drop_constraint(
        "fk_vclf_action",
        "video_classifications",
        type_="foreignkey",
    )
    op.drop_column("video_classifications", "action")
    op.drop_column("video_classifications", "category_l3")
    op.drop_column("video_classifications", "category_l2")
    op.drop_column("video_classifications", "category_l1")
    op.add_column(
        "video_classifications",
        sa.Column("tech_category", sa.String(50), nullable=True),
    )
    op.add_column(
        "video_classifications",
        sa.Column("tech_sub_category", sa.String(50), nullable=True),
    )
    op.add_column(
        "video_classifications",
        sa.Column("tech_detail", sa.String(50), nullable=True),
    )

    # ── Step 4 -> reverse: coach_video_classifications ────────────────
    op.drop_index("idx_cvclf_coach_action", table_name="coach_video_classifications")
    op.drop_index(
        "idx_cvclf_review_state_action",
        table_name="coach_video_classifications",
    )
    op.drop_index("idx_cvclf_action", table_name="coach_video_classifications")
    op.drop_constraint(
        "fk_cvclf_action",
        "coach_video_classifications",
        type_="foreignkey",
    )
    op.drop_column("coach_video_classifications", "action")
    op.drop_column("coach_video_classifications", "category_l3")
    op.drop_column("coach_video_classifications", "category_l2")
    op.drop_column("coach_video_classifications", "category_l1")
    op.add_column(
        "coach_video_classifications",
        sa.Column("tech_category", sa.String(64), nullable=True),
    )
    op.create_index(
        "idx_cvclf_tech",
        "coach_video_classifications",
        ["tech_category"],
    )
    op.create_index(
        "idx_cvclf_coach_tech",
        "coach_video_classifications",
        ["coach_name", "tech_category"],
    )
    op.create_index(
        "idx_cvclf_review_state_tech",
        "coach_video_classifications",
        ["review_state", "tech_category"],
    )

    # ── Step 1+2 -> reverse: DROP tech_actions 表 ─────────────────────
    op.drop_index("ix_tech_actions_l1l2l3", table_name="tech_actions")
    op.drop_table("tech_actions")
