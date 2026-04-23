"""Add diagnosis_reports and diagnosis_dimension_results tables

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-23 00:00:00.000000

Changes:
- Create ``diagnosis_reports`` table for anonymous motion diagnosis results
- Create ``diagnosis_dimension_results`` table for per-dimension comparison details
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgcrypto for gen_random_uuid() if not already enabled
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "diagnosis_reports",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tech_category", sa.VARCHAR(64), nullable=False),
        sa.Column(
            "standard_id",
            sa.BigInteger(),
            sa.ForeignKey("tech_standards.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("standard_version", sa.Integer(), nullable=False),
        sa.Column("video_path", sa.Text(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("strengths_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_dr_tech_category",
        "diagnosis_reports",
        ["tech_category"],
    )
    op.create_index(
        "idx_dr_created_at",
        "diagnosis_reports",
        [sa.text("created_at DESC")],
    )

    op.create_table(
        "diagnosis_dimension_results",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column(
            "report_id",
            UUID(as_uuid=True),
            sa.ForeignKey("diagnosis_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", sa.VARCHAR(128), nullable=False),
        sa.Column("measured_value", sa.Float(), nullable=False),
        sa.Column("ideal_value", sa.Float(), nullable=False),
        sa.Column("standard_min", sa.Float(), nullable=False),
        sa.Column("standard_max", sa.Float(), nullable=False),
        sa.Column("unit", sa.VARCHAR(32), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("deviation_level", sa.VARCHAR(20), nullable=False),
        sa.Column("deviation_direction", sa.VARCHAR(10), nullable=True),
        sa.Column("improvement_advice", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_id", "dimension", name="uq_ddr_report_dimension"
        ),
        sa.CheckConstraint(
            "deviation_level IN ('ok', 'slight', 'significant')",
            name="ck_ddr_deviation_level",
        ),
        sa.CheckConstraint(
            "deviation_direction IN ('above', 'below', 'none') OR deviation_direction IS NULL",
            name="ck_ddr_deviation_direction",
        ),
    )
    op.create_index(
        "idx_ddr_report",
        "diagnosis_dimension_results",
        ["report_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_ddr_report", table_name="diagnosis_dimension_results")
    op.drop_table("diagnosis_dimension_results")
    op.drop_index("idx_dr_created_at", table_name="diagnosis_reports")
    op.drop_index("idx_dr_tech_category", table_name="diagnosis_reports")
    op.drop_table("diagnosis_reports")
