"""Create teaching_tips table

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-20 00:00:00.000000

Changes:
- Create table ``teaching_tips`` for LLM-extracted coaching tip entries
- Add indexes on task_id, action_type, source_type for efficient filtering
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teaching_tips",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("tech_phase", sa.String(30), nullable=False),
        sa.Column("tip_text", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column(
            "source_type",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'auto'"),
        ),
        sa.Column("original_text", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_teaching_tip_confidence_range",
        ),
        sa.CheckConstraint(
            "source_type IN ('auto', 'human')",
            name="ck_teaching_tip_source_type",
        ),
    )

    op.create_index("ix_teaching_tips_task_id", "teaching_tips", ["task_id"])
    op.create_index("ix_teaching_tips_action_type", "teaching_tips", ["action_type"])
    op.create_index("ix_teaching_tips_source_type", "teaching_tips", ["source_type"])


def downgrade() -> None:
    op.drop_index("ix_teaching_tips_source_type", table_name="teaching_tips")
    op.drop_index("ix_teaching_tips_action_type", table_name="teaching_tips")
    op.drop_index("ix_teaching_tips_task_id", table_name="teaching_tips")
    op.drop_table("teaching_tips")
