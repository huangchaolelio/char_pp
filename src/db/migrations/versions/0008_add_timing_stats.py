"""Add timing_stats JSONB column to analysis_tasks

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-21 00:00:00.000000

Changes:
- Add ``timing_stats`` nullable JSONB column to ``analysis_tasks``
  Stores per-phase processing durations:
  {pre_split_s, pose_estimation_s, kb_extraction_s, total_s}
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_tasks",
        sa.Column("timing_stats", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_tasks", "timing_stats")
