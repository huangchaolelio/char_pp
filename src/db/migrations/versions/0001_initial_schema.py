"""Initial schema: all 6 tables

Revision ID: 0001
Revises:
Create Date: 2026-04-17 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── tech_knowledge_bases ────────────────────────────────────────────────
    op.create_table(
        "tech_knowledge_bases",
        sa.Column("version", sa.String(20), primary_key=True),
        sa.Column(
            "action_types_covered",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
        ),
        sa.Column("point_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.Enum("draft", "active", "archived", name="kb_status_enum"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    # ── analysis_tasks ──────────────────────────────────────────────────────
    op.create_table(
        "analysis_tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "task_type",
            sa.Enum("expert_video", "athlete_video", name="task_type_enum"),
            nullable=False,
        ),
        sa.Column("video_filename", sa.String(500), nullable=False),
        sa.Column("video_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("video_duration_seconds", sa.Float(), nullable=True),
        sa.Column("video_fps", sa.Float(), nullable=True),
        sa.Column("video_resolution", sa.String(20), nullable=True),
        # Stored encrypted at application layer
        sa.Column("video_storage_uri", sa.String(1000), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "success",
                "failed",
                "rejected",
                name="task_status_enum",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("knowledge_base_version", sa.String(20), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["knowledge_base_version"],
            ["tech_knowledge_bases.version"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "idx_task_status",
        "analysis_tasks",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_task_deleted",
        "analysis_tasks",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )

    # ── expert_tech_points ──────────────────────────────────────────────────
    op.create_table(
        "expert_tech_points",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("knowledge_base_version", sa.String(20), nullable=False),
        sa.Column(
            "action_type",
            sa.Enum(
                "forehand_topspin",
                "backhand_push",
                name="action_type_enum",
            ),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(100), nullable=False),
        sa.Column("param_min", sa.Float(), nullable=False),
        sa.Column("param_max", sa.Float(), nullable=False),
        sa.Column("param_ideal", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("extraction_confidence", sa.Float(), nullable=False),
        sa.Column(
            "source_video_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_version"],
            ["tech_knowledge_bases.version"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_video_id"],
            ["analysis_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "knowledge_base_version",
            "action_type",
            "dimension",
            name="uq_expert_point_version_action_dim",
        ),
    )
    op.create_index(
        "idx_expert_point_action_type",
        "expert_tech_points",
        ["action_type", "knowledge_base_version"],
    )
    op.create_index(
        "idx_expert_point_dimension",
        "expert_tech_points",
        ["dimension"],
    )

    # ── athlete_motion_analyses ─────────────────────────────────────────────
    op.create_table(
        "athlete_motion_analyses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "action_type",
            sa.Enum(
                "forehand_topspin",
                "backhand_push",
                "unknown",
                name="athlete_action_type_enum",
            ),
            nullable=False,
        ),
        sa.Column("segment_start_ms", sa.Integer(), nullable=False),
        sa.Column("segment_end_ms", sa.Integer(), nullable=False),
        sa.Column("measured_params", postgresql.JSONB(), nullable=False),
        sa.Column("overall_confidence", sa.Float(), nullable=False),
        sa.Column(
            "is_low_confidence",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("knowledge_base_version", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["analysis_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_version"],
            ["tech_knowledge_bases.version"],
            ondelete="RESTRICT",
        ),
    )

    # ── deviation_reports ───────────────────────────────────────────────────
    op.create_table(
        "deviation_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "expert_point_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(100), nullable=False),
        sa.Column("measured_value", sa.Float(), nullable=False),
        sa.Column("ideal_value", sa.Float(), nullable=False),
        sa.Column("deviation_value", sa.Float(), nullable=False),
        sa.Column(
            "deviation_direction",
            sa.Enum("above", "below", "none", name="deviation_direction_enum"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("is_low_confidence", sa.Boolean(), nullable=False),
        # NULL = insufficient samples; True = stable; False = occasional
        sa.Column("is_stable_deviation", sa.Boolean(), nullable=True),
        # [0,1] normalized impact score
        sa.Column("impact_score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["athlete_motion_analyses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["expert_point_id"],
            ["expert_tech_points.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "idx_deviation_action_dim",
        "deviation_reports",
        ["dimension"],
        postgresql_include=["analysis_id", "deviation_direction"],
    )

    # ── coaching_advice ─────────────────────────────────────────────────────
    op.create_table(
        "coaching_advice",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "deviation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("deviation_description", sa.Text(), nullable=False),
        sa.Column("improvement_target", sa.Text(), nullable=False),
        sa.Column("improvement_method", sa.Text(), nullable=False),
        sa.Column("impact_score", sa.Float(), nullable=False),
        sa.Column(
            "reliability_level",
            sa.Enum("high", "low", name="reliability_level_enum"),
            nullable=False,
        ),
        sa.Column("reliability_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["deviation_id"],
            ["deviation_reports.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["analysis_tasks.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_advice_task",
        "coaching_advice",
        ["task_id", sa.text("impact_score DESC")],
    )


def downgrade() -> None:
    op.drop_table("coaching_advice")
    op.drop_table("deviation_reports")
    op.drop_table("athlete_motion_analyses")
    op.drop_table("expert_tech_points")
    op.drop_table("analysis_tasks")
    op.drop_table("tech_knowledge_bases")
    # Drop enums
    for enum_name in [
        "reliability_level_enum",
        "deviation_direction_enum",
        "athlete_action_type_enum",
        "action_type_enum",
        "task_status_enum",
        "task_type_enum",
        "kb_status_enum",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
