"""Add missing task channel configs for Feature-020 athlete inference pipeline.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-01

Fix internal error in channel management API by adding missing config rows
for the two new task types added in migration 0018.
"""

from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add default configuration for the two new task types from Feature-020
    op.execute("""
        INSERT INTO task_channel_configs 
            (task_type, queue_capacity, concurrency, enabled, updated_at)
        VALUES
            ('athlete_video_classification', 10, 2, true, now()),
            ('athlete_video_preprocessing', 15, 2, true, now())
        ON CONFLICT (task_type) DO NOTHING
    """)


def downgrade() -> None:
    # Remove the added config rows
    op.execute("""
        DELETE FROM task_channel_configs 
        WHERE task_type IN ('athlete_video_classification', 'athlete_video_preprocessing')
    """)