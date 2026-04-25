"""Feature-016 — Video preprocessing pipeline.

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-25

Changes (data-model.md §8):
1. ALTER TYPE task_type_enum ADD VALUE 'video_preprocessing' (extends Feature-013 channels).
2. CREATE TABLE video_preprocessing_jobs (+ 3 indexes + partial unique on cos_object_key WHERE status='success').
3. CREATE TABLE video_preprocessing_segments (+ 1 index, unique on (job_id, segment_index)).
4. ADD COLUMN coach_video_classifications.preprocessed BOOLEAN NOT NULL DEFAULT false + index.
5. Seed task_channel_configs row for 'video_preprocessing' (concurrency=3, queue_capacity=20).

Downgrade reverses every step except the enum value add — PostgreSQL does
not support DROP VALUE on an ENUM without a rebuild, which is out of scope
for a simple rollback. The leftover enum value is harmless (no rows
reference it after the row-delete in step 5 downgrade).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Extend task_type_enum so TaskChannelConfig can host 'video_preprocessing'.
    # ``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction block,
    # so commit the current tx first, then add the value, then reopen.
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE task_type_enum ADD VALUE IF NOT EXISTS 'video_preprocessing'"
    )

    # ── 2. video_preprocessing_jobs
    op.create_table(
        "video_preprocessing_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("cos_object_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("force", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("segment_count", sa.Integer, nullable=True),
        sa.Column("original_meta_json", postgresql.JSONB, nullable=True),
        sa.Column("target_standard_json", postgresql.JSONB, nullable=True),
        sa.Column("audio_cos_object_key", sa.String(1024), nullable=True),
        sa.Column("audio_size_bytes", sa.BigInteger, nullable=True),
        sa.Column(
            "has_audio", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("local_artifact_dir", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'superseded')",
            name="ck_vpj_status",
        ),
    )
    op.create_index(
        "idx_vpj_status", "video_preprocessing_jobs", ["status"]
    )
    op.create_index(
        "idx_vpj_cos_object_key", "video_preprocessing_jobs", ["cos_object_key"]
    )
    op.create_index(
        "idx_vpj_created_at", "video_preprocessing_jobs", ["created_at"]
    )
    # Partial unique index — enforces FR-007 (at most one success row per cos_object_key).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_vpj_cos_success
        ON video_preprocessing_jobs (cos_object_key)
        WHERE status = 'success'
        """
    )

    # ── 3. video_preprocessing_segments
    op.create_table(
        "video_preprocessing_segments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "video_preprocessing_jobs.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("segment_index", sa.Integer, nullable=False),
        sa.Column("start_ms", sa.Integer, nullable=False),
        sa.Column("end_ms", sa.Integer, nullable=False),
        sa.Column("cos_object_key", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("job_id", "segment_index", name="uq_vps_job_index"),
        sa.CheckConstraint("end_ms > start_ms", name="ck_vps_timeline"),
        sa.CheckConstraint("size_bytes > 0", name="ck_vps_size"),
        sa.CheckConstraint("segment_index >= 0", name="ck_vps_index"),
    )
    op.create_index("idx_vps_job_id", "video_preprocessing_segments", ["job_id"])

    # ── 4. coach_video_classifications.preprocessed
    op.add_column(
        "coach_video_classifications",
        sa.Column(
            "preprocessed",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "idx_cvclf_preprocessed",
        "coach_video_classifications",
        ["preprocessed"],
    )

    # ── 5. Seed task_channel_configs
    op.execute(
        """
        INSERT INTO task_channel_configs
            (task_type, queue_capacity, concurrency, enabled, updated_at)
        VALUES
            ('video_preprocessing', 20, 3, true, now())
        ON CONFLICT (task_type) DO NOTHING
        """
    )


def downgrade() -> None:
    # ── 5. remove seed row
    op.execute(
        "DELETE FROM task_channel_configs WHERE task_type = 'video_preprocessing'"
    )

    # ── 4. drop preprocessed column
    op.drop_index(
        "idx_cvclf_preprocessed", table_name="coach_video_classifications"
    )
    op.drop_column("coach_video_classifications", "preprocessed")

    # ── 3. video_preprocessing_segments
    op.drop_index("idx_vps_job_id", table_name="video_preprocessing_segments")
    op.drop_table("video_preprocessing_segments")

    # ── 2. video_preprocessing_jobs
    op.execute("DROP INDEX IF EXISTS uq_vpj_cos_success")
    op.drop_index("idx_vpj_created_at", table_name="video_preprocessing_jobs")
    op.drop_index("idx_vpj_cos_object_key", table_name="video_preprocessing_jobs")
    op.drop_index("idx_vpj_status", table_name="video_preprocessing_jobs")
    op.drop_table("video_preprocessing_jobs")

    # ── 1. leave the enum value in place — PostgreSQL cannot DROP an ENUM
    # value without a full recreate, and the leftover value is harmless now
    # that no rows reference it.
