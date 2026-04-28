"""KB audit columns + expand action_type_enum to 21 TECH_CATEGORIES.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-28

Changes:
1. tech_knowledge_bases ADD COLUMN extraction_job_id UUID NULL
   (FK -> extraction_jobs.id ON DELETE SET NULL) + index.
   修复："KB 表中看不到 job_id" —— 原设计只把 job_id 塞在 notes 字符串里无法查询。
2. expert_tech_points ADD COLUMN submitted_tech_category VARCHAR(50) NULL
   (审计列；C2 方案：保留 visual 分类器输出作为 action_type，但同步记录
   提交时的 tech_category 供对账)。
3. ALTER TYPE action_type_enum 补齐到 21 类，与 TECH_CATEGORIES 对齐
   （B1 方案：消除 "提交 forehand_attack / 入库 backhand_push" 类枚举空间错配）。

Note: ALTER TYPE ... ADD VALUE cannot run inside a PostgreSQL transaction block.
      沿用 0004 的 DO $$ 条件写法保证幂等。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


# 21 类 TECH_CATEGORIES（src/services/tech_classifier.py::TECH_CATEGORIES 单一事实来源）。
# 已存在的旧值（见 0001/0004）：forehand_attack / forehand_topspin /
# forehand_chop_long / forehand_counter / forehand_loop_underspin /
# forehand_flick / forehand_position / forehand_general /
# backhand_push / backhand_topspin / backhand_flick / backhand_general
_TECH_CATEGORY_VALUES: list[str] = [
    "forehand_push_long",
    "forehand_attack",
    "forehand_topspin",
    "forehand_topspin_backspin",
    "forehand_loop_fast",
    "forehand_loop_high",
    "forehand_flick",
    "backhand_attack",
    "backhand_topspin",
    "backhand_topspin_backspin",
    "backhand_flick",
    "backhand_push",
    "serve",
    "receive",
    "footwork",
    "forehand_backhand_transition",
    "defense",
    "penhold_reverse",
    "stance_posture",
    "general",
    "unclassified",
]


def upgrade() -> None:
    # ── 1. tech_knowledge_bases.extraction_job_id
    op.add_column(
        "tech_knowledge_bases",
        sa.Column(
            "extraction_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_tech_kb_extraction_job",
        "tech_knowledge_bases",
        ["extraction_job_id"],
    )

    # ── 2. expert_tech_points.submitted_tech_category
    op.add_column(
        "expert_tech_points",
        sa.Column(
            "submitted_tech_category",
            sa.String(length=50),
            nullable=True,
        ),
    )

    # ── 3. 扩充 action_type_enum（按 TECH_CATEGORIES 补齐缺失项）。
    for val in _TECH_CATEGORY_VALUES:
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
    # 1. 回滚审计列
    op.drop_column("expert_tech_points", "submitted_tech_category")
    # 2. 回滚 tech_knowledge_bases.extraction_job_id
    op.drop_index("idx_tech_kb_extraction_job", table_name="tech_knowledge_bases")
    op.drop_column("tech_knowledge_bases", "extraction_job_id")
    # 3. PostgreSQL 不支持从 enum 中 DROP VALUE，新增的 enum 值保留（无害）。
