"""Add tech_standards and tech_standard_points tables

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-22 00:00:00.000000

Changes:
- Create ``tech_standards`` table for versioned per-technique standard records
- Create ``tech_standard_points`` table for per-dimension aggregated standard params
- Indexes for efficient querying by tech_category+status and standard_id
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tech_standards",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("tech_category", sa.VARCHAR(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "status",
            sa.VARCHAR(16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("source_quality", sa.VARCHAR(16), nullable=False),
        sa.Column("coach_count", sa.Integer(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column(
            "built_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tech_category", "version", name="uq_ts_tech_version"),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_ts_status"
        ),
        sa.CheckConstraint(
            "source_quality IN ('multi_source', 'single_source')",
            name="ck_ts_source_quality",
        ),
    )
    op.create_index(
        "idx_ts_tech_status",
        "tech_standards",
        ["tech_category", "status"],
    )
    op.create_index(
        "idx_ts_tech_version",
        "tech_standards",
        ["tech_category", sa.text("version DESC")],
    )

    op.create_table(
        "tech_standard_points",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column(
            "standard_id",
            sa.BigInteger(),
            sa.ForeignKey("tech_standards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", sa.VARCHAR(128), nullable=False),
        sa.Column("ideal", sa.Float(), nullable=False),
        sa.Column("min", sa.Float(), nullable=False),
        sa.Column("max", sa.Float(), nullable=False),
        sa.Column("unit", sa.VARCHAR(32), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("coach_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "standard_id", "dimension", name="uq_tsp_standard_dimension"
        ),
    )
    op.create_index(
        "idx_tsp_standard",
        "tech_standard_points",
        ["standard_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_tsp_standard", table_name="tech_standard_points")
    op.drop_table("tech_standard_points")
    op.drop_index("idx_ts_tech_version", table_name="tech_standards")
    op.drop_index("idx_ts_tech_status", table_name="tech_standards")
    op.drop_table("tech_standards")
