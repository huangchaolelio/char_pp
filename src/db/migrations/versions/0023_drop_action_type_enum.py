"""Feature 审计修复（G2）— 删除 expert_tech_points.action_type ENUM 列，
改为 varchar(64) + FK→tech_actions(action) 与 V2 字典对齐.

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-02

背景:
    Feature-019 + Feature-023 已经把 ``tech_knowledge_bases`` / ``teaching_tips`` /
    ``coach_video_classifications`` 等表的"动作"字段统一迁到 ``action`` (varchar(64))
    + FK→``tech_actions``。但 ``expert_tech_points.action_type`` 这一列被遗漏了，
    仍是 PG enum ``action_type_enum``（V1 词表 27 个值）。merge_kb 在写入时被迫做
    V1↔V2 映射，凡是 V2 中文动作（如"高吊弧圈球"）一律落入 fallback / 被丢弃，
    实际入库的 expert_tech_points 行数恒为 0。

修复:
    1. DROP COLUMN expert_tech_points.action_type
    2. DROP TYPE action_type_enum  (此列是该枚举的最后使用方)
    3. ADD COLUMN expert_tech_points.action varchar(64) NOT NULL
       + FK→tech_actions(action) ON DELETE RESTRICT
       + 索引 ix_expert_tech_points_action
    4. ALTER COLUMN submitted_action 从 varchar(50) → varchar(64)
       （与 kb_action / action / tech_actions.action 口径对齐，V2 中文动作长度
        可能超过 50 — 比如"前冲弧圈球" 5×3=15B 不超，但保险起见统一 64）

前置条件:
    expert_tech_points 必须为空（system-init 已执行）。表非空时 ADD COLUMN NOT NULL
    会失败 — 这是设计上的强制护栏，避免遗留脏数据混入 V2 字典。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 0. 前置护栏：expert_tech_points 必须为空 ─────────────────────────
    bind = op.get_bind()
    n = bind.execute(sa.text("SELECT COUNT(*) FROM expert_tech_points")).scalar_one()
    if n != 0:
        raise RuntimeError(
            f"expert_tech_points 期望为 0 行，实际 {n} 行；"
            "请先执行 system-init skill 清空业务数据后再升级 0023"
        )

    # ── 1. 删旧索引 + 旧列 + 旧枚举类型 ───────────────────────────────────
    # 0001_initial_schema 创建过 idx_expert_point_action_type；不一定还在（
    # 中间迁移可能已经删过），用 IF EXISTS 守一下。
    op.execute("DROP INDEX IF EXISTS idx_expert_point_action_type")
    op.drop_column("expert_tech_points", "action_type")
    op.execute("DROP TYPE IF EXISTS action_type_enum")

    # ── 2. 新增 action 列 + 复合 FK（与 Feature-023 其他业务表口径一致） ────────
    # NOT NULL 但表已被清空，安全；新写入由 merge_kb 提供 action 值。
    # 复合 FK (category_l1, l2, l3, action) → tech_actions(...)：与
    # coach_video_classifications / tech_knowledge_bases / tech_standards /
    # teaching_tips / diagnosis_reports 保持同一形态。category_l1/l2/l3 在
    # expert_tech_points 上仍为 nullable —— PG MATCH SIMPLE 语义：任一 FK 列
    # 为 NULL 时不强制校验，与其他表行为一致。
    op.add_column(
        "expert_tech_points",
        sa.Column("action", sa.String(64), nullable=False),
    )
    op.create_foreign_key(
        "fk_expert_tech_points_action",
        "expert_tech_points",
        "tech_actions",
        ["category_l1", "category_l2", "category_l3", "action"],
        ["category_l1", "category_l2", "category_l3", "action"],
        ondelete="RESTRICT",
        onupdate="CASCADE",
    )
    op.create_index(
        "ix_expert_tech_points_action",
        "expert_tech_points",
        ["action"],
    )

    # ── 3. submitted_action 列长度对齐到 64 ───────────────────────────────
    op.alter_column(
        "expert_tech_points",
        "submitted_action",
        existing_type=sa.String(50),
        type_=sa.String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    """回滚到 0022：恢复 action_type_enum + action_type 列，删除 action 列.

    回滚同样要求表为空（无法把 V2 中文 action 字符串映射回 V1 enum）。
    """
    bind = op.get_bind()
    n = bind.execute(sa.text("SELECT COUNT(*) FROM expert_tech_points")).scalar_one()
    if n != 0:
        raise RuntimeError(
            f"expert_tech_points 期望为 0 行，实际 {n} 行；"
            "回滚要求表为空（无法将 V2 action 字符串映射回 V1 枚举）"
        )

    # 还原 submitted_action 长度
    op.alter_column(
        "expert_tech_points",
        "submitted_action",
        existing_type=sa.String(64),
        type_=sa.String(50),
        existing_nullable=True,
    )

    # 删 action 索引 + FK + 列
    op.drop_index("ix_expert_tech_points_action", table_name="expert_tech_points")
    op.drop_constraint(
        "fk_expert_tech_points_action", "expert_tech_points", type_="foreignkey"
    )
    op.drop_column("expert_tech_points", "action")

    # 重建 V1 枚举 + action_type 列（27 个值，复刻 0001+0004+0015 的最终状态）
    action_type_values = [
        # 0001 initial
        "forehand_topspin",
        "backhand_push",
        # 0004 expand
        "forehand_attack",
        "forehand_chop_long",
        "forehand_counter",
        "forehand_loop_underspin",
        "forehand_position",
        "forehand_general",
        "backhand_attack",
        "backhand_general",
        "serve",
        "footwork",
        # 0015 align to TECH_CATEGORIES（21 类）
        "forehand_push_long",
        "forehand_topspin_backspin",
        "forehand_loop_fast",
        "forehand_loop_high",
        "forehand_flick",
        "backhand_topspin",
        "backhand_topspin_backspin",
        "backhand_flick",
        "receive",
        "forehand_backhand_transition",
        "defense",
        "penhold_reverse",
        "stance_posture",
        "general",
        "unclassified",
    ]
    enum_values_sql = ", ".join(f"'{v}'" for v in action_type_values)
    op.execute(f"CREATE TYPE action_type_enum AS ENUM ({enum_values_sql})")

    op.add_column(
        "expert_tech_points",
        sa.Column(
            "action_type",
            sa.Enum(*action_type_values, name="action_type_enum", create_type=False),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_expert_point_action_type",
        "expert_tech_points",
        ["action_type"],
    )
