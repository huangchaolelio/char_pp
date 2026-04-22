"""Feature 003: Skill KB to Reference Video — data model

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-20 00:00:00.000000

Changes:
- New table: skills
- New table: skill_executions  (+ execution_status_enum)
- New table: reference_videos
- New table: reference_video_segments
- New indexes on skill_executions and reference_videos
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── skills ───────────────────────────────────────────────────────────────
    op.create_table(
        "skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("action_types", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("video_source_config", postgresql.JSONB(), nullable=False),
        sa.Column("enable_audio", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("audio_language", sa.String(10), nullable=False, server_default="zh"),
        sa.Column(
            "extra_config",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", name="uq_skills_name"),
    )

    # ── execution_status_enum + skill_executions ──────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE execution_status_enum AS ENUM
                ('pending', 'running', 'success', 'failed', 'approved', 'rejected');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    op.create_table(
        "skill_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "running", "success", "failed", "approved", "rejected",
                name="execution_status_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("skill_config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "kb_version",
            sa.String(20),
            sa.ForeignKey("tech_knowledge_bases.version", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_skill_executions_skill_status",
        "skill_executions",
        ["skill_id", "status"],
    )

    # ── reference_videos ─────────────────────────────────────────────────────
    op.create_table(
        "reference_videos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skill_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kb_version",
            sa.String(20),
            sa.ForeignKey("tech_knowledge_bases.version"),
            nullable=False,
        ),
        sa.Column(
            "generation_status",
            sa.String(30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("cos_key", sa.Text, nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("total_dimensions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("included_dimensions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("execution_id", name="uq_reference_videos_execution_id"),
    )
    op.create_index(
        "ix_reference_videos_execution_id",
        "reference_videos",
        ["execution_id"],
    )

    # ── reference_video_segments ──────────────────────────────────────────────
    op.create_table(
        "reference_video_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "reference_video_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference_videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_order", sa.Integer, nullable=False),
        sa.Column("dimension", sa.String(100), nullable=False),
        sa.Column("label_text", sa.Text, nullable=False),
        sa.Column("source_video_cos_key", sa.Text, nullable=False),
        sa.Column("source_start_ms", sa.Integer, nullable=False),
        sa.Column("source_end_ms", sa.Integer, nullable=False),
        sa.Column("extraction_confidence", sa.Float, nullable=False),
        sa.Column(
            "conflict_flag", sa.Boolean, nullable=False, server_default="false"
        ),
    )


def downgrade() -> None:
    # ── reference_video_segments ──────────────────────────────────────────────
    op.drop_table("reference_video_segments")

    # ── reference_videos ─────────────────────────────────────────────────────
    op.drop_index("ix_reference_videos_execution_id", table_name="reference_videos")
    op.drop_table("reference_videos")

    # ── skill_executions ─────────────────────────────────────────────────────
    op.drop_index("ix_skill_executions_skill_status", table_name="skill_executions")
    op.drop_table("skill_executions")
    op.execute("DROP TYPE IF EXISTS execution_status_enum")

    # ── skills ───────────────────────────────────────────────────────────────
    op.drop_table("skills")
