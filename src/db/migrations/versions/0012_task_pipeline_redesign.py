"""Feature 013 — Task pipeline redesign: 3-value task_type enum, new columns, channel config table.

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-24 20:15:00.000000

Changes (data-model.md § Alembic 0012):
1. TRUNCATE analysis_tasks CASCADE (historical data already pg_dump'd to /tmp/feature013_backup/).
2. DROP TYPE task_type_enum CASCADE (removes old `expert_video`/`athlete_video` values).
3. CREATE TYPE task_type_enum with 3 values: video_classification / kb_extraction / athlete_diagnosis.
4. ALTER TABLE analysis_tasks: restore task_type column with new enum.
5. ADD COLUMN cos_object_key VARCHAR(1000) NULL.
6. ADD COLUMN submitted_via VARCHAR(20) NOT NULL DEFAULT 'single'.
7. ADD COLUMN parent_scan_task_id UUID NULL (self-FK, ON DELETE SET NULL).
8. CREATE partial unique index idx_analysis_tasks_idempotency
   ON (cos_object_key, task_type) WHERE status IN ('pending','processing','success').
9. CREATE INDEX idx_analysis_tasks_channel_counting ON (task_type, status).
10. CREATE TABLE task_channel_configs with 3 default rows.

Downgrade is DESTRUCTIVE — never run on production without full pg_dump of
analysis_tasks + dependent tables; the old enum values cannot be reconstructed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Purge historical task rows (backup lives at /tmp/feature013_backup/)
    #    CASCADE to children: expert_tech_points, audio_transcripts,
    #    tech_semantic_segments, athlete_motion_analyses, coaching_advice, teaching_tips.
    op.execute("TRUNCATE TABLE analysis_tasks CASCADE")

    # ── 2+3. Rebuild task_type_enum with 3-value set.
    #    Drop the column first (CASCADE would take dependent views; we have none).
    op.execute("ALTER TABLE analysis_tasks DROP COLUMN task_type")
    op.execute("DROP TYPE task_type_enum")
    op.execute(
        "CREATE TYPE task_type_enum AS ENUM "
        "('video_classification', 'kb_extraction', 'athlete_diagnosis')"
    )

    # ── 4. Restore task_type column with new enum.
    op.add_column(
        "analysis_tasks",
        sa.Column(
            "task_type",
            postgresql.ENUM(
                "video_classification",
                "kb_extraction",
                "athlete_diagnosis",
                name="task_type_enum",
                create_type=False,
            ),
            nullable=False,
        ),
    )

    # ── 5. COS object key (nullable — only for classification & kb_extraction rows).
    op.add_column(
        "analysis_tasks",
        sa.Column("cos_object_key", sa.String(length=1000), nullable=True),
    )

    # ── 6. Submission channel: 'single' | 'batch' | 'scan'.
    op.add_column(
        "analysis_tasks",
        sa.Column(
            "submitted_via",
            sa.String(length=20),
            nullable=False,
            server_default="single",
        ),
    )

    # ── 7. parent_scan_task_id: self-FK, set only when submitted_via='scan'.
    op.add_column(
        "analysis_tasks",
        sa.Column(
            "parent_scan_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_analysis_tasks_parent_scan",
        "analysis_tasks",
        ["parent_scan_task_id"],
        postgresql_where=sa.text("parent_scan_task_id IS NOT NULL"),
    )

    # ── 8. Idempotency guard: one live (cos_object_key, task_type) in pending/processing/success.
    #    failed/rejected/partial_success rows do NOT block re-submission.
    op.create_index(
        "idx_analysis_tasks_idempotency",
        "analysis_tasks",
        ["cos_object_key", "task_type"],
        unique=True,
        postgresql_where=sa.text(
            "cos_object_key IS NOT NULL AND status IN ('pending','processing','success')"
        ),
    )

    # ── 9. Channel counting accelerator for TaskSubmissionService limit check.
    op.create_index(
        "idx_analysis_tasks_channel_counting",
        "analysis_tasks",
        ["task_type", "status"],
    )

    # ── 10. task_channel_configs — dynamic capacity/concurrency tuning.
    op.create_table(
        "task_channel_configs",
        sa.Column(
            "task_type",
            postgresql.ENUM(
                "video_classification",
                "kb_extraction",
                "athlete_diagnosis",
                name="task_type_enum",
                create_type=False,
            ),
            primary_key=True,
        ),
        sa.Column("queue_capacity", sa.Integer(), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("queue_capacity > 0", name="ck_task_channel_capacity_positive"),
        sa.CheckConstraint("concurrency > 0", name="ck_task_channel_concurrency_positive"),
    )
    op.execute(
        """
        INSERT INTO task_channel_configs (task_type, queue_capacity, concurrency, enabled)
        VALUES
            ('video_classification', 5, 1, TRUE),
            ('kb_extraction', 50, 2, TRUE),
            ('athlete_diagnosis', 20, 2, TRUE)
        """
    )


def downgrade() -> None:
    # Destructive — restores pre-013 shape but cannot recover historical rows.
    op.drop_table("task_channel_configs")
    op.drop_index("idx_analysis_tasks_channel_counting", table_name="analysis_tasks")
    op.drop_index("idx_analysis_tasks_idempotency", table_name="analysis_tasks")
    op.drop_index("idx_analysis_tasks_parent_scan", table_name="analysis_tasks")
    op.drop_column("analysis_tasks", "parent_scan_task_id")
    op.drop_column("analysis_tasks", "submitted_via")
    op.drop_column("analysis_tasks", "cos_object_key")
    op.execute("ALTER TABLE analysis_tasks DROP COLUMN task_type")
    op.execute("DROP TYPE task_type_enum")
    op.execute("CREATE TYPE task_type_enum AS ENUM ('expert_video', 'athlete_video')")
    op.add_column(
        "analysis_tasks",
        sa.Column(
            "task_type",
            postgresql.ENUM(
                "expert_video", "athlete_video", name="task_type_enum", create_type=False
            ),
            nullable=False,
        ),
    )
