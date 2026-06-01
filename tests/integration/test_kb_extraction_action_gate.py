"""Feature-023 — KB extraction action gate 集成测试.

T039:
  - test_kb_extraction_blocked_when_action_unclassified
  - test_kb_extraction_proceeds_with_valid_action
  - test_kb_extraction_persists_action_to_expert_tech_points (smoke)

策略：直接驱动 ClassificationGateService + KbExtractionService（绕过 Celery），
真实 DB session（asyncpg 直连构造 fixture，避开跨 loop 问题）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg
import pytest

from src.services.classification_gate_service import ClassificationGateService
from src.services.kb_extraction_service import KbExtractionService


pytestmark = pytest.mark.asyncio

_DSN = "postgresql://postgres:password@localhost:5432/coaching_db"


# ── Helpers ──────────────────────────────────────────────────────────


async def _seed_classification(
    cos_key: str,
    *,
    action: str | None,
    coach_name: str = "test_coach",
) -> uuid.UUID:
    """直接 asyncpg 插入 coach_video_classifications + coaches 行."""
    conn = await asyncpg.connect(_DSN)
    try:
        await conn.execute(
            "INSERT INTO coaches (id, name, is_active) VALUES (gen_random_uuid(), $1, true) "
            "ON CONFLICT (name) DO NOTHING",
            coach_name,
        )
        new_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO coach_video_classifications
                (id, coach_name, course_series, cos_object_key, filename,
                 category_l1, category_l2, category_l3, action,
                 tech_tags, classification_source, confidence,
                 name_source, kb_extracted, created_at, updated_at)
            VALUES ($1, $2, 'test_series', $3, $4,
                    $5, $6, $7, $8,
                    ARRAY[]::text[], 'rule', 1.0,
                    'fallback', false,
                    timezone('Asia/Shanghai', now()),
                    timezone('Asia/Shanghai', now()))
            """,
            new_id,
            coach_name,
            cos_key,
            cos_key.rsplit("/", 1)[-1],
            "横拍" if action and action != "unclassified" else None,
            "反胶" if action and action != "unclassified" else None,
            "正手·进攻" if action and action != "unclassified" else None,
            action,
        )
    finally:
        await conn.close()
    return new_id


async def _cleanup(cos_key: str) -> None:
    conn = await asyncpg.connect(_DSN)
    try:
        await conn.execute(
            "DELETE FROM coach_video_classifications WHERE cos_object_key = $1",
            cos_key,
        )
    finally:
        await conn.close()


def _build_session_factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(
        _DSN.replace("postgresql://", "postgresql+asyncpg://"),
        pool_pre_ping=False,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


# ── 测试用例 ──────────────────────────────────────────────────────────


async def test_kb_extraction_blocked_when_action_unclassified() -> None:
    """门禁：action='unclassified' 时 gate.check_classified() 返回 False."""
    cos_key = f"test/gate_v2/{uuid.uuid4()}.mp4"
    await _seed_classification(cos_key, action="unclassified")
    try:
        engine, SessionFactory = _build_session_factory()
        try:
            async with SessionFactory() as session:
                gate = ClassificationGateService()
                allowed = await gate.check_classified(session, cos_key)
                assert allowed is False
                cur = await gate.get_action(session, cos_key)
                assert cur == "unclassified"
        finally:
            await engine.dispose()
    finally:
        await _cleanup(cos_key)


async def test_kb_extraction_proceeds_with_valid_action() -> None:
    """门禁：action='高吊弧圈球'（字典内）时 gate 返回 True，KB 提取可推进."""
    cos_key = f"test/gate_v2/{uuid.uuid4()}.mp4"
    await _seed_classification(cos_key, action="高吊弧圈球")
    try:
        engine, SessionFactory = _build_session_factory()
        try:
            async with SessionFactory() as session:
                gate = ClassificationGateService()
                allowed = await gate.check_classified(session, cos_key)
                assert allowed is True
                cur = await gate.get_action(session, cos_key)
                assert cur == "高吊弧圈球"
        finally:
            await engine.dispose()
    finally:
        await _cleanup(cos_key)


async def test_kb_extraction_persists_action_to_classification_row() -> None:
    """烟雾测试：KbExtractionService.extract_knowledge 翻转 kb_extracted=True，返回 action."""
    cos_key = f"test/gate_v2/{uuid.uuid4()}.mp4"
    await _seed_classification(cos_key, action="前冲弧圈球")
    try:
        engine, SessionFactory = _build_session_factory()
        try:
            async with SessionFactory() as session:
                svc = KbExtractionService()
                summary = await svc.extract_knowledge(session, cos_key)
                assert summary["action"] == "前冲弧圈球"
                assert summary["kb_extracted"] is True
                assert summary["cos_object_key"] == cos_key
        finally:
            await engine.dispose()

        # 二次校验：DB 中该行 kb_extracted=true
        conn = await asyncpg.connect(_DSN)
        try:
            row = await conn.fetchrow(
                "SELECT kb_extracted, action FROM coach_video_classifications WHERE cos_object_key = $1",
                cos_key,
            )
        finally:
            await conn.close()
        assert row["kb_extracted"] is True
        assert row["action"] == "前冲弧圈球"
    finally:
        await _cleanup(cos_key)
