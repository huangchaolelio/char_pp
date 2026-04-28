"""Feature 002: audio transcripts, tech semantic segments, and model field extensions

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-19 00:00:00.000000

Changes:
- New table: audio_transcripts
- New table: tech_semantic_segments
- Extend expert_tech_points: source_type, transcript_segment_id, conflict_flag, conflict_detail
- Extend analysis_tasks: total_segments, processed_segments, progress_pct, audio_fallback_reason
- Extend task_status_enum: add partial_success value
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── audio_quality_flag_enum ──────────────────────────────────────────────
    # Use DO block to skip if already exists (idempotent for repeated runs)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE audio_quality_flag_enum AS ENUM ('ok', 'low_snr', 'unsupported_language', 'silent');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    # ── audio_transcripts ────────────────────────────────────────────────────
    op.create_table(
        "audio_transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column("total_duration_s", sa.Float, nullable=True),
        sa.Column("snr_db", sa.Float, nullable=True),
        sa.Column(
            "quality_flag",
            postgresql.ENUM(
                "ok", "low_snr", "unsupported_language", "silent",
                name="audio_quality_flag_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="ok",
        ),
        sa.Column("fallback_reason", sa.Text, nullable=True),
        sa.Column("sentences", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
        ),
    )
    op.create_index("ix_audio_transcripts_task_id", "audio_transcripts", ["task_id"])

    # ── tech_semantic_segments ───────────────────────────────────────────────
    op.create_table(
        "tech_semantic_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transcript_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audio_transcripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_ms", sa.Integer, nullable=False),
        sa.Column("end_ms", sa.Integer, nullable=False),
        sa.Column("priority_window_start_ms", sa.Integer, nullable=True),
        sa.Column("priority_window_end_ms", sa.Integer, nullable=True),
        sa.Column("trigger_keyword", sa.String(100), nullable=True),
        sa.Column("source_sentence", sa.Text, nullable=False),
        sa.Column("dimension", sa.String(100), nullable=True),
        sa.Column("param_min", sa.Float, nullable=True),
        sa.Column("param_max", sa.Float, nullable=True),
        sa.Column("param_ideal", sa.Float, nullable=True),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("parse_confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("is_reference_note", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            nullable=False,
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
        ),
    )
    op.create_index("ix_tech_semantic_segments_transcript_id", "tech_semantic_segments", ["transcript_id"])
    op.create_index("ix_tech_semantic_segments_task_id", "tech_semantic_segments", ["task_id"])

    # ── expert_tech_points extensions ────────────────────────────────────────
    op.add_column(
        "expert_tech_points",
        sa.Column("source_type", sa.String(20), nullable=False, server_default="visual"),
    )
    op.add_column(
        "expert_tech_points",
        sa.Column(
            "transcript_segment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tech_semantic_segments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "expert_tech_points",
        sa.Column("conflict_flag", sa.Boolean, nullable=False, server_default="false"),
    )
    op.add_column(
        "expert_tech_points",
        sa.Column("conflict_detail", postgresql.JSONB, nullable=True),
    )

    # ── analysis_tasks extensions ────────────────────────────────────────────
    op.add_column(
        "analysis_tasks",
        sa.Column("total_segments", sa.Integer, nullable=True),
    )
    op.add_column(
        "analysis_tasks",
        sa.Column("processed_segments", sa.Integer, nullable=True),
    )
    op.add_column(
        "analysis_tasks",
        sa.Column("progress_pct", sa.Float, nullable=True),
    )
    op.add_column(
        "analysis_tasks",
        sa.Column("audio_fallback_reason", sa.Text, nullable=True),
    )

    # ── task_status_enum: add partial_success ────────────────────────────────
    op.execute("ALTER TYPE task_status_enum ADD VALUE IF NOT EXISTS 'partial_success'")


def downgrade() -> None:
    # ── analysis_tasks: remove added columns ─────────────────────────────────
    op.drop_column("analysis_tasks", "audio_fallback_reason")
    op.drop_column("analysis_tasks", "progress_pct")
    op.drop_column("analysis_tasks", "processed_segments")
    op.drop_column("analysis_tasks", "total_segments")

    # ── expert_tech_points: remove added columns ─────────────────────────────
    op.drop_column("expert_tech_points", "conflict_detail")
    op.drop_column("expert_tech_points", "conflict_flag")
    op.drop_column("expert_tech_points", "transcript_segment_id")
    op.drop_column("expert_tech_points", "source_type")

    # ── tech_semantic_segments ───────────────────────────────────────────────
    op.drop_index("ix_tech_semantic_segments_task_id", table_name="tech_semantic_segments")
    op.drop_index("ix_tech_semantic_segments_transcript_id", table_name="tech_semantic_segments")
    op.drop_table("tech_semantic_segments")

    # ── audio_transcripts ────────────────────────────────────────────────────
    op.drop_index("ix_audio_transcripts_task_id", table_name="audio_transcripts")
    op.drop_table("audio_transcripts")

    # ── audio_quality_flag_enum ──────────────────────────────────────────────
    op.execute("DROP TYPE IF EXISTS audio_quality_flag_enum")
