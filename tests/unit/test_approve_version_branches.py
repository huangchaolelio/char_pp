"""Feature-019 US1 T015 — knowledge_base_svc.approve_version 6 分支单元测试.

覆盖分支:
  (a) 该类别首批 approve（无旧 active）→ previous_active_version=None
  (b) 同类别切换 active → previous_active_version=旧 version
  (c) 不同 tc 的 active 完全不受影响（跨类别隔离核心）
  (d) 目标记录不存在 → AppException(KB_VERSION_NOT_FOUND)
  (e) 目标记录非 draft → AppException(KB_VERSION_NOT_DRAFT)
  (f) 目标记录 point_count=0 → AppException(KB_EMPTY_POINTS)

每个测试在真实 DB 上运行，避免 mock 掩盖边界。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from src.api.errors import AppException, ErrorCode
from src.db import session as _db_session
from src.services import knowledge_base_svc


@pytest.fixture(autouse=True)
async def _rebind_engine():
    await _db_session.engine.dispose()
    _db_session.engine = _db_session._make_engine()
    _db_session.AsyncSessionFactory.kw["bind"] = _db_session.engine
    yield
    await _db_session.engine.dispose()


def _sess():
    return _db_session.AsyncSessionFactory()


async def _reset() -> None:
    async with _sess() as session:
        for tbl in (
            "teaching_tips",
            "expert_tech_points",
            "tech_knowledge_bases",
            "extraction_jobs",
        ):
            await session.execute(sa.text(f"DELETE FROM {tbl}"))
        await session.execute(sa.text(
            "DELETE FROM analysis_tasks WHERE task_type='kb_extraction'"
        ))
        await session.commit()


async def _seed_kb(
    tech_category: str,
    version: int,
    status: str = "draft",
    point_count: int = 3,
) -> None:
    async with _sess() as session:
        tid = uuid.uuid4()
        jid = uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO analysis_tasks "
                "(id, task_type, video_filename, video_size_bytes, video_storage_uri, "
                " status, submitted_via, business_phase, business_step) "
                "VALUES (:tid, 'kb_extraction', 's.mp4', 1, 'cos://s', "
                "        'success', 'single', 'TRAINING', 'extract_kb')"
            ),
            {"tid": tid},
        )
        await session.execute(
            sa.text(
                "INSERT INTO extraction_jobs "
                "(id, analysis_task_id, status, cos_object_key, tech_category, "
                " business_phase, business_step) "
                "VALUES (:jid, :tid, 'success', 'cos://s', :tc, "
                "        'TRAINING', 'extract_kb')"
            ),
            {"jid": jid, "tid": tid, "tc": tech_category},
        )
        await session.execute(
            sa.text(
                "INSERT INTO tech_knowledge_bases "
                "(tech_category, version, status, point_count, extraction_job_id, "
                " business_phase, business_step) "
                "VALUES (:tc, :ver, :st, :pc, :jid, "
                "        'STANDARDIZATION', 'kb_version_activate')"
            ),
            {"tc": tech_category, "ver": version, "st": status, "pc": point_count, "jid": jid},
        )
        await session.commit()


# ══════════════════════════════════════════════════════════════════════════
# 分支 (a): 首批 approve
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_first_approve_no_previous():
    await _reset()
    await _seed_kb("forehand_attack", 1, "draft", point_count=3)

    async with _sess() as session:
        async with session.begin():
            result = await knowledge_base_svc.approve_version(
                session, "forehand_attack", 1, "coach_zhang"
            )

    assert result["previous_active_version"] is None
    assert result["new_active"].status.value == "active"


# ══════════════════════════════════════════════════════════════════════════
# 分支 (b): 同类别切换
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_same_category_switch_archives_previous():
    await _reset()
    await _seed_kb("backhand_topspin", 1, "active", point_count=3)
    await _seed_kb("backhand_topspin", 2, "draft", point_count=5)

    async with _sess() as session:
        async with session.begin():
            result = await knowledge_base_svc.approve_version(
                session, "backhand_topspin", 2, "coach_li"
            )

    assert result["previous_active_version"] == 1


# ══════════════════════════════════════════════════════════════════════════
# 分支 (c): 跨类别隔离核心
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cross_category_isolation():
    await _reset()
    await _seed_kb("forehand_attack", 1, "active", point_count=3)
    await _seed_kb("backhand_topspin", 1, "draft", point_count=3)

    async with _sess() as session:
        async with session.begin():
            await knowledge_base_svc.approve_version(
                session, "backhand_topspin", 1, "coach_zhang"
            )

    # forehand_attack 仍保持 active
    async with _sess() as session:
        row = await session.execute(sa.text(
            "SELECT status FROM tech_knowledge_bases "
            "WHERE tech_category='forehand_attack' AND version=1"
        ))
        assert row.scalar_one() == "active"


# ══════════════════════════════════════════════════════════════════════════
# 分支 (d): KB_VERSION_NOT_FOUND
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_not_found_raises():
    await _reset()

    async with _sess() as session:
        with pytest.raises(AppException) as exc:
            async with session.begin():
                await knowledge_base_svc.approve_version(
                    session, "serve", 99, "coach"
                )
        assert exc.value.code == ErrorCode.KB_VERSION_NOT_FOUND


# ══════════════════════════════════════════════════════════════════════════
# 分支 (e): KB_VERSION_NOT_DRAFT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_not_draft_raises():
    await _reset()
    await _seed_kb("defense", 1, "active", point_count=3)

    async with _sess() as session:
        with pytest.raises(AppException) as exc:
            async with session.begin():
                await knowledge_base_svc.approve_version(
                    session, "defense", 1, "coach"
                )
        assert exc.value.code == ErrorCode.KB_VERSION_NOT_DRAFT


# ══════════════════════════════════════════════════════════════════════════
# 分支 (f): KB_EMPTY_POINTS
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_empty_points_raises():
    await _reset()
    await _seed_kb("receive", 1, "draft", point_count=0)

    async with _sess() as session:
        with pytest.raises(AppException) as exc:
            async with session.begin():
                await knowledge_base_svc.approve_version(
                    session, "receive", 1, "coach"
                )
        assert exc.value.code == ErrorCode.KB_EMPTY_POINTS
