"""Expand action_type_enum with fine-grained technique types

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-20 00:00:00.000000

Changes:
- ALTER TYPE action_type_enum ADD VALUE for 10 new technique types:
    forehand_attack, forehand_chop_long, forehand_counter,
    forehand_loop_underspin, forehand_flick, forehand_position,
    forehand_general, backhand_topspin, backhand_flick, backhand_general

Note: ALTER TYPE ... ADD VALUE cannot run inside a PostgreSQL transaction.
      We use a DO $$ block that wraps each ADD VALUE with a conditional check
      to achieve idempotency without requiring AUTOCOMMIT mode.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_NEW_VALUES = [
    "forehand_attack",
    "forehand_chop_long",
    "forehand_counter",
    "forehand_loop_underspin",
    "forehand_flick",
    "forehand_position",
    "forehand_general",
    "backhand_topspin",
    "backhand_flick",
    "backhand_general",
]


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    # Use a DO $$ block that checks pg_enum before adding to avoid errors
    # when re-running. The DO block itself runs outside the enum-change restriction.
    for val in _NEW_VALUES:
        op.execute(f"""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_enum
                    WHERE enumtypid = 'action_type_enum'::regtype
                      AND enumlabel = '{val}'
                ) THEN
                    ALTER TYPE action_type_enum ADD VALUE '{val}';
                END IF;
            END $$;
        """)


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    # Downgrade is intentionally a no-op; manual intervention required if needed.
    pass
