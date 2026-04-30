"""Feature-020 — 运动员推理流水线数据模型落地.

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-30

对齐 specs/020-athlete-inference-pipeline/data-model.md § 7 迁移清单：
1. CREATE TABLE athletes（与 coaches 结构对称，独立表）
2. CREATE TABLE athlete_video_classifications（与 coach_video_classifications 独立）
3. ALTER TABLE diagnosis_reports ADD 3 列 `cos_object_key` / `preprocessing_job_id` / `source`
4. CREATE 2 索引 `ix_dr_cos_object_key_created_at` / `ix_dr_preprocessing_job_id`
5. ALTER TYPE task_type_enum ADD VALUE 2 次（ENUM 值仅 up 不 down，PostgreSQL 不支持 DROP VALUE）

禁止与教练侧表合并；禁止在已发布列上做破坏性 ALTER（章程原则 IX "只允许新增"）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


# ── 常量 ────────────────────────────────────────────────────────────
_CST_NOW = sa.text("timezone('Asia/Shanghai', now())")
_DR_CHECK_NAME = "ck_dr_source"


def upgrade() -> None:
    # ── 1. athletes 表 ───────────────────────────────────────────────
    op.create_table(
        "athletes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column(
            "created_via",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'athlete_scan'"),
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
        sa.UniqueConstraint("name", name="uq_athletes_name"),
    )

    # ── 2. athlete_video_classifications 表 + 4 索引 ─────────────────
    op.create_table(
        "athlete_video_classifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("cos_object_key", sa.String(1024), nullable=False),
        sa.Column(
            "athlete_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("athletes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("athlete_name", sa.String(100), nullable=False),
        sa.Column("name_source", sa.String(10), nullable=False),
        sa.Column("tech_category", sa.String(50), nullable=False),
        sa.Column("classification_source", sa.String(10), nullable=False),
        sa.Column("classification_confidence", sa.Float(), nullable=False),
        sa.Column(
            "preprocessed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "preprocessing_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("video_preprocessing_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "last_diagnosis_report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("diagnosis_reports.id", ondelete="SET NULL"),
            nullable=True,
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
        sa.UniqueConstraint("cos_object_key", name="uq_avclf_cos_object_key"),
        sa.CheckConstraint(
            "name_source IN ('map', 'fallback')",
            name="ck_avclf_name_source",
        ),
        sa.CheckConstraint(
            "classification_source IN ('rule', 'llm', 'fallback')",
            name="ck_avclf_classification_source",
        ),
        sa.CheckConstraint(
            "classification_confidence >= 0.0 AND classification_confidence <= 1.0",
            name="ck_avclf_confidence_range",
        ),
    )
    op.create_index(
        "ix_avclf_athlete_created",
        "athlete_video_classifications",
        ["athlete_id", "created_at"],
    )
    op.create_index(
        "ix_avclf_tech_created",
        "athlete_video_classifications",
        ["tech_category", "created_at"],
    )
    op.create_index(
        "ix_avclf_preprocessed_tech",
        "athlete_video_classifications",
        ["preprocessed", "tech_category"],
    )

    # ── 3. diagnosis_reports 扩列 3 列 ───────────────────────────────
    op.add_column(
        "diagnosis_reports",
        sa.Column("cos_object_key", sa.String(1024), nullable=True),
    )
    op.add_column(
        "diagnosis_reports",
        sa.Column(
            "preprocessing_job_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "diagnosis_reports",
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'legacy'"),
        ),
    )
    op.create_foreign_key(
        "fk_dr_preprocessing_job",
        "diagnosis_reports",
        "video_preprocessing_jobs",
        ["preprocessing_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        _DR_CHECK_NAME,
        "diagnosis_reports",
        "source IN ('legacy', 'athlete_pipeline')",
    )

    # ── 4. diagnosis_reports 2 索引 ──────────────────────────────────
    op.create_index(
        "ix_dr_cos_object_key_created_at",
        "diagnosis_reports",
        ["cos_object_key", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_dr_preprocessing_job_id",
        "diagnosis_reports",
        ["preprocessing_job_id"],
    )

    # ── 5. ENUM 扩展（仅 up 不 down）──────────────────────────────────
    op.execute(
        "ALTER TYPE task_type_enum ADD VALUE IF NOT EXISTS "
        "'athlete_video_classification'"
    )
    op.execute(
        "ALTER TYPE task_type_enum ADD VALUE IF NOT EXISTS "
        "'athlete_video_preprocessing'"
    )


def downgrade() -> None:
    # ── 4. 先删 diagnosis_reports 2 索引 ─────────────────────────────
    op.drop_index("ix_dr_preprocessing_job_id", table_name="diagnosis_reports")
    op.drop_index("ix_dr_cos_object_key_created_at", table_name="diagnosis_reports")

    # ── 3. 删 diagnosis_reports 约束 + 3 列 ──────────────────────────
    op.drop_constraint(_DR_CHECK_NAME, "diagnosis_reports", type_="check")
    op.drop_constraint("fk_dr_preprocessing_job", "diagnosis_reports", type_="foreignkey")
    op.drop_column("diagnosis_reports", "source")
    op.drop_column("diagnosis_reports", "preprocessing_job_id")
    op.drop_column("diagnosis_reports", "cos_object_key")

    # ── 2. athlete_video_classifications 索引 + 表 ───────────────────
    op.drop_index("ix_avclf_preprocessed_tech", table_name="athlete_video_classifications")
    op.drop_index("ix_avclf_tech_created", table_name="athlete_video_classifications")
    op.drop_index("ix_avclf_athlete_created", table_name="athlete_video_classifications")
    op.drop_table("athlete_video_classifications")

    # ── 1. athletes 表 ───────────────────────────────────────────────
    op.drop_table("athletes")

    # ── 5. task_type_enum 新值不 downgrade（PostgreSQL 不支持 DROP VALUE FROM ENUM）
    # 历史行可能已引用这两个值；章程亦不鼓励破坏性迁移 down。保留枚举值残留可接受。
