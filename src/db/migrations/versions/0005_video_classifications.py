"""Create video_classifications table

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-20 00:00:00.000000

Changes:
- Create table ``video_classifications`` with full COS object key as primary key
- Add indexes on tech_category, action_type, coach_name for efficient filtering
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_classifications",
        sa.Column("cos_object_key", sa.String(500), primary_key=True),
        sa.Column("coach_name", sa.String(100), nullable=False),
        sa.Column("tech_category", sa.String(50), nullable=False),
        sa.Column("tech_sub_category", sa.String(50), nullable=True),
        sa.Column("tech_detail", sa.String(50), nullable=True),
        sa.Column("video_type", sa.String(20), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=True),
        sa.Column("classification_confidence", sa.Float, nullable=False),
        sa.Column(
            "manually_overridden",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("override_reason", sa.Text, nullable=True),
        sa.Column(
            "classified_at",
            TIMESTAMP(timezone=False),
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=False),
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_video_classifications_tech_category",
        "video_classifications",
        ["tech_category"],
    )
    op.create_index(
        "ix_video_classifications_action_type",
        "video_classifications",
        ["action_type"],
    )
    op.create_index(
        "ix_video_classifications_coach_name",
        "video_classifications",
        ["coach_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_classifications_coach_name", table_name="video_classifications")
    op.drop_index("ix_video_classifications_action_type", table_name="video_classifications")
    op.drop_index("ix_video_classifications_tech_category", table_name="video_classifications")
    op.drop_table("video_classifications")
