"""Feature 014 — KB extraction pipeline: extraction_jobs / pipeline_steps / kb_conflicts.

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-24 22:00:00.000000

Changes (data-model.md § Alembic 0013):
1. CREATE TYPE extraction_job_status AS ENUM ('pending','running','success','failed').
2. CREATE TYPE pipeline_step_status  AS ENUM ('pending','running','success','failed','skipped').
3. CREATE TYPE pipeline_step_type    AS ENUM (6 StepType values).
4. CREATE TABLE extraction_jobs.
5. CREATE TABLE pipeline_steps.
6. CREATE TABLE kb_conflicts.
7. ADD COLUMN analysis_tasks.extraction_job_id UUID NULL (FK -> extraction_jobs, ON DELETE SET NULL).
8. CREATE supporting indexes (listed in data-model.md).

Downgrade drops all three tables + enums + the analysis_tasks column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


EXTRACTION_JOB_STATUS_VALUES = ("pending", "running", "success", "failed")
PIPELINE_STEP_STATUS_VALUES = ("pending", "running", "success", "failed", "skipped")
PIPELINE_STEP_TYPE_VALUES = (
    "download_video",
    "pose_analysis",
    "audio_transcription",
    "visual_kb_extract",
    "audio_kb_extract",
    "merge_kb",
)


def upgrade() -> None:
    # ── 1-3. Enum types.
    extraction_job_status = postgresql.ENUM(
        *EXTRACTION_JOB_STATUS_VALUES,
        name="extraction_job_status",
        create_type=True,
    )
    pipeline_step_status = postgresql.ENUM(
        *PIPELINE_STEP_STATUS_VALUES,
        name="pipeline_step_status",
        create_type=True,
    )
    pipeline_step_type = postgresql.ENUM(
        *PIPELINE_STEP_TYPE_VALUES,
        name="pipeline_step_type",
        create_type=True,
    )
    bind = op.get_bind()
    extraction_job_status.create(bind, checkfirst=True)
    pipeline_step_status.create(bind, checkfirst=True)
    pipeline_step_type.create(bind, checkfirst=True)

    # ── 4. extraction_jobs.
    op.create_table(
        "extraction_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "analysis_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("cos_object_key", sa.String(length=512), nullable=False),
        sa.Column("tech_category", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                *EXTRACTION_JOB_STATUS_VALUES,
                name="extraction_job_status",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("worker_hostname", sa.String(length=100), nullable=True),
        sa.Column(
            "enable_audio_analysis",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "audio_language",
            sa.String(length=10),
            nullable=False,
            server_default="zh",
        ),
        sa.Column(
            "force",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "superseded_by_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("intermediate_cleanup_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_extraction_jobs_status",
        "extraction_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_extraction_jobs_cos_key_active",
        "extraction_jobs",
        ["cos_object_key"],
        postgresql_where=sa.text("status IN ('pending','running')"),
    )

    # ── 5. pipeline_steps.
    op.create_table(
        "pipeline_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "step_type",
            postgresql.ENUM(
                *PIPELINE_STEP_TYPE_VALUES,
                name="pipeline_step_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                *PIPELINE_STEP_STATUS_VALUES,
                name="pipeline_step_status",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "retry_count",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("output_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_artifact_path", sa.String(length=1000), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.UniqueConstraint("job_id", "step_type", name="uq_pipeline_steps_job_step"),
    )
    op.create_index(
        "idx_pipeline_steps_running_orphan",
        "pipeline_steps",
        ["started_at"],
        postgresql_where=sa.text("status = 'running'"),
    )

    # ── 6. kb_conflicts.
    op.create_table(
        "kb_conflicts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cos_object_key", sa.String(length=512), nullable=False),
        sa.Column("tech_category", sa.String(length=50), nullable=False),
        sa.Column("dimension_name", sa.String(length=200), nullable=False),
        sa.Column("visual_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("audio_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("visual_confidence", sa.Float(), nullable=True),
        sa.Column("audio_confidence", sa.Float(), nullable=True),
        sa.Column(
            "superseded_by_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolution", sa.String(length=20), nullable=True),
        sa.Column("resolution_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_kb_conflicts_job", "kb_conflicts", ["job_id"])
    op.create_index(
        "idx_kb_conflicts_pending",
        "kb_conflicts",
        ["cos_object_key", "created_at"],
        postgresql_where=sa.text("resolved_at IS NULL AND superseded_by_job_id IS NULL"),
    )

    # ── 7. analysis_tasks.extraction_job_id column.
    op.add_column(
        "analysis_tasks",
        sa.Column(
            "extraction_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analysis_tasks", "extraction_job_id")
    op.drop_index("idx_kb_conflicts_pending", table_name="kb_conflicts")
    op.drop_index("idx_kb_conflicts_job", table_name="kb_conflicts")
    op.drop_table("kb_conflicts")
    op.drop_index("idx_pipeline_steps_running_orphan", table_name="pipeline_steps")
    op.drop_table("pipeline_steps")
    op.drop_index("idx_extraction_jobs_cos_key_active", table_name="extraction_jobs")
    op.drop_index("idx_extraction_jobs_status", table_name="extraction_jobs")
    op.drop_table("extraction_jobs")
    bind = op.get_bind()
    postgresql.ENUM(name="pipeline_step_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="pipeline_step_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="extraction_job_status").drop(bind, checkfirst=True)
