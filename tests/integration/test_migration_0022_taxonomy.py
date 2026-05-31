"""Feature-023 — 0022_tech_taxonomy_rebuild 迁移集成测试.

T010: 端到端验证迁移 upgrade 后的 schema 形态.

⚠️ 此测试使用真实 PostgreSQL 数据库，**前置条件**：
   1. 业务表已 TRUNCATE（system-init skill 已执行）
   2. alembic_version 当前应已 upgrade 到 0022

实现说明：使用 asyncpg 直连而非 SQLAlchemy 的 AsyncSessionFactory，
避免 pytest-asyncio 函数级事件循环与 sessionmaker 全局连接池跨 loop 冲突.
"""

from __future__ import annotations

import asyncpg
import pytest


pytestmark = pytest.mark.asyncio

_DSN = "postgresql://postgres:password@localhost:5432/coaching_db"


async def _scalar(sql: str, *params) -> int:
    """Helper：每次新建 asyncpg 连接执行单次查询，避免连接池跨 loop 复用."""
    conn = await asyncpg.connect(_DSN)
    try:
        result = await conn.fetchval(sql, *params)
        return result if result is not None else 0
    finally:
        await conn.close()


async def _scalar_str(sql: str) -> str | None:
    conn = await asyncpg.connect(_DSN)
    try:
        return await conn.fetchval(sql)
    finally:
        await conn.close()


async def test_tech_actions_table_seeded_with_56_rows() -> None:
    """tech_actions 字典表应有 56 行 seed."""
    cnt = await _scalar("SELECT count(*) FROM tech_actions")
    assert cnt == 56, f"tech_actions 期望 56 行，实际 {cnt} 行"


async def test_tech_actions_no_zwsp_in_data() -> None:
    """seed 时已 strip U+200B 零宽字符；验证字典内无残留."""
    cnt = await _scalar(
        "SELECT count(*) FROM tech_actions "
        "WHERE position(E'\u200b' in action) > 0 "
        "   OR position(E'\u200b' in category_l1) > 0 "
        "   OR position(E'\u200b' in category_l2) > 0 "
        "   OR position(E'\u200b' in category_l3) > 0"
    )
    assert cnt == 0, f"期望无 ZWSP 残留，实际 {cnt} 行"


async def test_tech_category_column_dropped_from_business_tables() -> None:
    """7 张业务表的旧 tech_category 列必须物理消失."""
    tables = (
        "expert_tech_points",
        "tech_knowledge_bases",
        "tech_standards",
        "teaching_tips",
        "diagnosis_reports",
        "coach_video_classifications",
        "video_classifications",
    )
    for tbl in tables:
        cnt = await _scalar(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = 'tech_category'",
            tbl,
        )
        assert cnt == 0, f"{tbl}.tech_category 列未删除"


async def test_four_level_columns_added() -> None:
    """7 张业务表必须新增 category_l1/l2/l3 三级列."""
    expected_tables = (
        "coach_video_classifications",
        "video_classifications",
        "expert_tech_points",
        "tech_knowledge_bases",
        "tech_standards",
        "teaching_tips",
        "diagnosis_reports",
    )
    for tbl in expected_tables:
        for col in ("category_l1", "category_l2", "category_l3"):
            cnt = await _scalar(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = $1 AND column_name = $2",
                tbl,
                col,
            )
            assert cnt == 1, f"{tbl}.{col} 缺失"


async def test_tech_kb_pk_renamed() -> None:
    """tech_knowledge_bases 的复合主键应已从 pk_tech_kb_cat_ver 重命名为 pk_tech_kb_action_ver."""
    new_pk = await _scalar(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'pk_tech_kb_action_ver'"
    )
    assert new_pk == 1, "新 PK pk_tech_kb_action_ver 不存在"

    old_pk = await _scalar(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'pk_tech_kb_cat_ver'"
    )
    assert old_pk == 0, "旧 PK pk_tech_kb_cat_ver 仍存在（应已删除）"


async def test_kb_per_action_unique_index_exists() -> None:
    """单 active 约束 partial unique index 改名为 uq_tech_kb_active_per_action."""
    cnt = await _scalar(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_tech_kb_active_per_action'"
    )
    assert cnt == 1, "uq_tech_kb_active_per_action 索引不存在"


async def test_kb_action_columns_renamed_in_subtables() -> None:
    """5+1 张子表的 kb_tech_category → kb_action 列重命名."""
    subtables = (
        "expert_tech_points",
        "analysis_tasks",
        "teaching_tips",
        "reference_videos",
        "skill_executions",
        "athlete_motion_analyses",
    )
    for tbl in subtables:
        new_col = await _scalar(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = 'kb_action'",
            tbl,
        )
        assert new_col == 1, f"{tbl}.kb_action 列不存在"

        old_col = await _scalar(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = 'kb_tech_category'",
            tbl,
        )
        assert old_col == 0, f"{tbl}.kb_tech_category 列未删除（应已 RENAME）"


async def test_alembic_version_at_0022() -> None:
    """alembic_version 表当前应指向 0022."""
    ver = await _scalar_str("SELECT version_num FROM alembic_version")
    assert ver == "0022", f"alembic_version 期望 0022，实际 {ver}"


async def test_tech_actions_distinct_quads_eq_56() -> None:
    """字典表 distinct (l1,l2,l3,action) 四元组应严格 = 56."""
    cnt = await _scalar(
        "SELECT count(DISTINCT (category_l1, category_l2, category_l3, action)) "
        "FROM tech_actions"
    )
    assert cnt == 56, f"distinct 四元组期望 56，实际 {cnt}"
