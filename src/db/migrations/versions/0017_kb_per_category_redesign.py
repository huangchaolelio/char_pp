"""Feature-019 — KB per-category lifecycle redesign.

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-30

对齐 specs/019-kb-per-category-lifecycle/:
  - spec.md FR-001~FR-030
  - data-model.md（迁移骨架）
  - research.md R6（禁用 DROP ... CASCADE，走显式 drop_constraint + drop_table）
  - tasks.md T003 (upgrade) + T004 (downgrade) + T030a (tech_standards.source_fingerprint)

核心变更：
1. DROP 5 张表指向 tech_knowledge_bases 的 FK 约束（显式命名，不用 CASCADE）
2. DROP tech_knowledge_bases + 重建为复合主键 (tech_category, version INTEGER)
3. 5 张 FK 引用表：
    - expert_tech_points            : knowledge_base_version → kb_tech_category + kb_version (NOT NULL, CASCADE)
    - analysis_tasks                : knowledge_base_version → kb_tech_category + kb_version (NULL, SET NULL)
    - reference_videos              : kb_version             → kb_tech_category + kb_version (NOT NULL, RESTRICT)
    - skill_executions              : kb_version             → kb_tech_category + kb_version (NULL, SET NULL)
    - athlete_motion_analyses       : knowledge_base_version → kb_tech_category + kb_version (NOT NULL, RESTRICT)
4. teaching_tips 重构（先 DELETE 清空 → 加 4 新列 NOT NULL → 加复合 FK）
5. tech_standards.source_fingerprint CHAR(64) NULL + partial unique index（T030a 合并入）

⚠️ 系统未上线假设：upgrade/downgrade 均不保数据。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


# ────────────────────────────────────────────────────────────────────────────
# 5 张 FK 引用表的元数据（(table_name, old_col, new_nullable, ondelete, fk_constraint_name)）
_FK_TABLES: list[tuple[str, str, bool, str, str]] = [
    ("expert_tech_points", "knowledge_base_version", False, "CASCADE",
     "expert_tech_points_knowledge_base_version_fkey"),
    ("analysis_tasks", "knowledge_base_version", True, "SET NULL",
     "analysis_tasks_knowledge_base_version_fkey"),
    ("reference_videos", "kb_version", False, "RESTRICT",
     "reference_videos_kb_version_fkey"),
    ("skill_executions", "kb_version", True, "SET NULL",
     "skill_executions_kb_version_fkey"),
    ("athlete_motion_analyses", "knowledge_base_version", False, "RESTRICT",
     "athlete_motion_analyses_knowledge_base_version_fkey"),
]


def upgrade() -> None:
    # ── 1. 显式摘除 5 张 FK 引用表指向 tech_knowledge_bases 的外键（禁用 CASCADE）
    for table, _old_col, _null, _ondel, fk_name in _FK_TABLES:
        op.drop_constraint(fk_name, table, type_="foreignkey")

    # ── 2. DROP tech_knowledge_bases 并重建为复合主键结构 ────────────────
    # 先 DROP 表上的相关索引（如 idx_tech_kb_extraction_job 等）防残留
    op.execute("DROP TABLE IF EXISTS tech_knowledge_bases")
    op.execute("DROP TYPE IF EXISTS kb_status_enum")
    op.execute("CREATE TYPE kb_status_enum AS ENUM ('draft', 'active', 'archived')")

    op.create_table(
        "tech_knowledge_bases",
        sa.Column("tech_category", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="kb_status_enum", create_type=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("point_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extraction_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=False), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
        ),
        sa.Column(
            "business_phase",
            postgresql.ENUM(name="business_phase_enum", create_type=False),
            nullable=False,
            server_default="STANDARDIZATION",
        ),
        sa.Column(
            "business_step",
            sa.String(64),
            nullable=False,
            server_default="kb_version_activate",
        ),
        sa.PrimaryKeyConstraint("tech_category", "version", name="pk_tech_kb_cat_ver"),
        sa.ForeignKeyConstraint(
            ["extraction_job_id"],
            ["extraction_jobs.id"],
            ondelete="RESTRICT",
            name="fk_tech_kb_extraction_job",
        ),
        sa.CheckConstraint("version >= 1", name="ck_tech_kb_version_positive"),
        sa.CheckConstraint("point_count >= 0", name="ck_tech_kb_point_count_nn"),
    )
    op.create_index(
        "idx_tech_kb_extraction_job", "tech_knowledge_bases", ["extraction_job_id"]
    )
    op.create_index("idx_tech_kb_status", "tech_knowledge_bases", ["status"])
    op.execute(
        "CREATE UNIQUE INDEX uq_tech_kb_active_per_category "
        "ON tech_knowledge_bases (tech_category) WHERE status = 'active'"
    )

    # ── 3. 为 5 张 FK 引用表 DROP 旧单列 + ADD 新复合列 + 重建 FK ──────────
    for table, old_col, nullable, ondel, _fk_name in _FK_TABLES:
        # 先 drop 旧单列（其索引自动随 column 一起掉）
        op.drop_column(table, old_col)
        # 添加新复合列；若 NOT NULL，server_default 暂置 'unclassified'/1 兜底
        # （随后 T013 / system-init 会清空全部表行；实际产线首版会由 persist_kb.py 重新填值）
        if nullable:
            op.add_column(table, sa.Column("kb_tech_category", sa.String(64), nullable=True))
            op.add_column(table, sa.Column("kb_version", sa.Integer, nullable=True))
        else:
            # 先清空表，避免 NOT NULL + 现有行冲突（系统未上线假设）
            op.execute(f"DELETE FROM {table}")
            op.add_column(
                table,
                sa.Column("kb_tech_category", sa.String(64), nullable=False),
            )
            op.add_column(
                table,
                sa.Column("kb_version", sa.Integer, nullable=False),
            )
        op.create_foreign_key(
            f"fk_{table}_kb",
            table,
            "tech_knowledge_bases",
            ["kb_tech_category", "kb_version"],
            ["tech_category", "version"],
            ondelete=ondel,
        )

    # ── 4. teaching_tips 重构 ───────────────────────────────────────────
    # 清空表：新列为 NOT NULL 且 FK 必须指向真实 KB 行，不可能回填
    op.execute("DELETE FROM teaching_tips")
    op.execute("CREATE TYPE tip_status_enum AS ENUM ('draft', 'active', 'archived')")

    # 删老 action_type 列 + 对应索引
    op.execute("DROP INDEX IF EXISTS ix_teaching_tips_action_type")
    op.drop_column("teaching_tips", "action_type")

    op.add_column(
        "teaching_tips",
        sa.Column("tech_category", sa.String(64), nullable=False),
    )
    op.add_column(
        "teaching_tips",
        sa.Column("kb_tech_category", sa.String(64), nullable=False),
    )
    op.add_column(
        "teaching_tips",
        sa.Column("kb_version", sa.Integer, nullable=False),
    )
    op.add_column(
        "teaching_tips",
        sa.Column(
            "status",
            postgresql.ENUM(name="tip_status_enum", create_type=False),
            nullable=False,
            server_default="draft",
        ),
    )
    # 放宽 task_id 为 NULL（tips 生命周期与 task 解耦）
    op.alter_column("teaching_tips", "task_id", nullable=True)

    op.create_foreign_key(
        "fk_teaching_tips_kb",
        "teaching_tips",
        "tech_knowledge_bases",
        ["kb_tech_category", "kb_version"],
        ["tech_category", "version"],
        ondelete="CASCADE",
    )
    op.create_index("ix_teaching_tips_tech_category", "teaching_tips", ["tech_category"])
    op.create_index("ix_teaching_tips_status", "teaching_tips", ["status"])
    op.create_index(
        "ix_teaching_tips_kb", "teaching_tips", ["kb_tech_category", "kb_version"]
    )

    # ── 5. tech_standards.source_fingerprint（T030a 合并入 0017）────────
    op.add_column(
        "tech_standards",
        sa.Column("source_fingerprint", sa.String(64), nullable=True),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_ts_fingerprint_per_category "
        "ON tech_standards (tech_category, source_fingerprint) WHERE status = 'active'"
    )


def downgrade() -> None:
    # ⚠️ 不保数据（系统未上线假设）；仅还原 schema 到 Feature-018 基线（0016）

    # ── 5'. 回滚 tech_standards.source_fingerprint
    op.execute("DROP INDEX IF EXISTS uq_ts_fingerprint_per_category")
    op.drop_column("tech_standards", "source_fingerprint")

    # ── 4'. teaching_tips 回滚（清空 → 删新列 → 回填 action_type）
    op.execute("DELETE FROM teaching_tips")
    op.drop_constraint("fk_teaching_tips_kb", "teaching_tips", type_="foreignkey")
    op.drop_index("ix_teaching_tips_kb", table_name="teaching_tips")
    op.drop_index("ix_teaching_tips_status", table_name="teaching_tips")
    op.drop_index("ix_teaching_tips_tech_category", table_name="teaching_tips")
    op.alter_column("teaching_tips", "task_id", nullable=False)
    op.drop_column("teaching_tips", "status")
    op.drop_column("teaching_tips", "kb_version")
    op.drop_column("teaching_tips", "kb_tech_category")
    op.drop_column("teaching_tips", "tech_category")
    op.execute("DROP TYPE IF EXISTS tip_status_enum")

    op.add_column(
        "teaching_tips",
        sa.Column("action_type", sa.String(50), nullable=False, server_default="general"),
    )
    op.create_index("ix_teaching_tips_action_type", "teaching_tips", ["action_type"])

    # ── 3'. 5 张 FK 引用表：DROP 复合列 → ADD 回旧单列 → 重建旧 FK
    for table, old_col, nullable, ondel, fk_name in _FK_TABLES:
        op.drop_constraint(f"fk_{table}_kb", table, type_="foreignkey")
        op.drop_column(table, "kb_version")
        op.drop_column(table, "kb_tech_category")
        if not nullable:
            # 先清表再加 NOT NULL 老列
            op.execute(f"DELETE FROM {table}")
        op.add_column(
            table,
            sa.Column(old_col, sa.String(20), nullable=nullable),
        )
        # 注意 downgrade 期间 tech_knowledge_bases 还没回老 schema（下一步才重建），
        # 所以 FK 暂不在此回填；等 tech_knowledge_bases 重建后再统一补

    # ── 2'. 重建老的 tech_knowledge_bases（单列 version VARCHAR 主键）
    op.execute("DROP INDEX IF EXISTS uq_tech_kb_active_per_category")
    op.drop_table("tech_knowledge_bases")
    op.execute("DROP TYPE IF EXISTS kb_status_enum")
    op.execute("CREATE TYPE kb_status_enum AS ENUM ('draft', 'active', 'archived')")

    op.create_table(
        "tech_knowledge_bases",
        sa.Column("version", sa.String(20), primary_key=True),
        sa.Column(
            "action_types_covered",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
        ),
        sa.Column("point_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "status",
            postgresql.ENUM(name="kb_status_enum", create_type=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=False), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("extraction_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "business_phase",
            postgresql.ENUM(name="business_phase_enum", create_type=False),
            nullable=False,
            server_default="STANDARDIZATION",
        ),
        sa.Column(
            "business_step",
            sa.String(64),
            nullable=False,
            server_default="kb_version_activate",
        ),
        sa.CheckConstraint(
            "version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name="ck_kb_version_semver",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_job_id"],
            ["extraction_jobs.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "idx_tech_kb_extraction_job", "tech_knowledge_bases", ["extraction_job_id"]
    )

    # ── 1'. 重建 5 张 FK 引用表指向 tech_knowledge_bases.version 的单列 FK
    for table, old_col, nullable, ondel, fk_name in _FK_TABLES:
        op.create_foreign_key(
            fk_name,
            table,
            "tech_knowledge_bases",
            [old_col],
            ["version"],
            ondelete=ondel,
        )
