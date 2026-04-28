"""Add coaches table and coach_id to analysis_tasks

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-21 00:00:00.000000

Changes:
- Create table ``coaches`` with name UNIQUE constraint and is_active soft-delete flag
- Add ``coach_id`` nullable FK column to ``analysis_tasks``
- Add indexes for efficient coach-based queries
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create coaches table
    op.create_table(
        "coaches",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("bio", sa.Text, nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=False),
            nullable=False,
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
        ),
        sa.UniqueConstraint("name", name="uq_coaches_name"),
    )

    op.create_index("ix_coaches_name", "coaches", ["name"], unique=True)
    op.create_index("ix_coaches_is_active", "coaches", ["is_active"])

    # 2. Add coach_id to analysis_tasks
    op.add_column(
        "analysis_tasks",
        sa.Column(
            "coach_id",
            UUID(as_uuid=True),
            sa.ForeignKey("coaches.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_analysis_tasks_coach_id", "analysis_tasks", ["coach_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_tasks_coach_id", table_name="analysis_tasks")
    op.drop_column("analysis_tasks", "coach_id")

    op.drop_index("ix_coaches_is_active", table_name="coaches")
    op.drop_index("ix_coaches_name", table_name="coaches")
    op.drop_table("coaches")
