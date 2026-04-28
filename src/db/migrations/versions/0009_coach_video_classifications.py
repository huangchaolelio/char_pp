"""Add coach_video_classifications table

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-21 00:00:00.000000

Changes:
- Create ``coach_video_classifications`` table for coach video tech classification
- Add indexes for coach_name, tech_category, kb_extracted, (coach_name, tech_category)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coach_video_classifications",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("coach_name", sa.VARCHAR(100), nullable=False),
        sa.Column("course_series", sa.VARCHAR(255), nullable=False),
        sa.Column("cos_object_key", sa.VARCHAR(1024), nullable=False),
        sa.Column("filename", sa.VARCHAR(255), nullable=False),
        sa.Column("tech_category", sa.VARCHAR(64), nullable=False),
        sa.Column(
            "tech_tags",
            ARRAY(TEXT()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("raw_tech_desc", sa.VARCHAR(255), nullable=True),
        sa.Column(
            "classification_source",
            sa.VARCHAR(10),
            server_default=sa.text("'rule'"),
            nullable=False,
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            server_default=sa.text("1.0"),
            nullable=False,
        ),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column(
            "name_source",
            sa.VARCHAR(10),
            server_default=sa.text("'map'"),
            nullable=False,
        ),
        sa.Column(
            "kb_extracted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=False),
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cos_object_key"),
    )
    op.create_index(
        "idx_cvclf_coach",
        "coach_video_classifications",
        ["coach_name"],
    )
    op.create_index(
        "idx_cvclf_tech",
        "coach_video_classifications",
        ["tech_category"],
    )
    op.create_index(
        "idx_cvclf_kb",
        "coach_video_classifications",
        ["kb_extracted"],
    )
    op.create_index(
        "idx_cvclf_coach_tech",
        "coach_video_classifications",
        ["coach_name", "tech_category"],
    )


def downgrade() -> None:
    op.drop_index("idx_cvclf_coach_tech", table_name="coach_video_classifications")
    op.drop_index("idx_cvclf_kb", table_name="coach_video_classifications")
    op.drop_index("idx_cvclf_tech", table_name="coach_video_classifications")
    op.drop_index("idx_cvclf_coach", table_name="coach_video_classifications")
    op.drop_table("coach_video_classifications")
