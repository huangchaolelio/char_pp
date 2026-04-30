"""Feature-019 US3 T029 — TechStandardBuilder per-category 3 分支单元测试.

对齐 spec.md FR-014~FR-019，覆盖:
  (a) 该类别首次 build（指纹不存在）→ 成功，写入 active standard + fingerprint
  (b) 相同 active KB + 相同 points → AppException(STANDARD_ALREADY_UP_TO_DATE)
  (c) 无 active KB 的类别 → AppException(NO_ACTIVE_KB_FOR_CATEGORY)
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from src.api.errors import AppException, ErrorCode
from src.db import session as _db_session
from src.services.tech_standard_builder import TechStandardBuilder


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
            "tech_standard_points",
            "tech_standards",
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


async def _seed_active_kb_with_points(
    *,
    tech_category: str,
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
                "VALUES (:tc, 1, 'active', :pc, :jid, "
                "        'STANDARDIZATION', 'kb_version_activate')"
            ),
            {"tc": tech_category, "pc": point_count, "jid": jid},
        )
        for i in range(point_count):
            await session.execute(
                sa.text(
                    "INSERT INTO expert_tech_points "
                    "(id, kb_tech_category, kb_version, action_type, dimension, "
                    " param_min, param_max, param_ideal, unit, extraction_confidence, "
                    " source_video_id, source_type, conflict_flag) "
                    "VALUES (gen_random_uuid(), :tc, 1, :at, :dim, "
                    "        10.0, 20.0, 15.0, 'deg', 0.85, "
                    "        :tid, 'visual', false)"
                ),
                {"tc": tech_category, "at": tech_category, "dim": f"dim_{i}", "tid": tid},
            )
        await session.commit()


# ══════════════════════════════════════════════════════════════════════════
# 分支 (a): 首次 build
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_first_build_success_writes_fingerprint():
    await _reset()
    await _seed_active_kb_with_points(tech_category="forehand_attack", point_count=3)

    async with _sess() as session:
        async with session.begin():
            builder = TechStandardBuilder(session)
            result = await builder.build_standard("forehand_attack")

    assert result.result == "success"
    assert result.version == 1
    assert result.dimension_count == 3

    # 验证 fingerprint 已写入
    async with _sess() as session:
        row = (await session.execute(sa.text(
            "SELECT source_fingerprint, status FROM tech_standards "
            "WHERE tech_category='forehand_attack' AND version=1"
        ))).one()
        assert row[0] is not None
        assert len(row[0]) == 64  # sha256 hex
        assert row[1] == "active"


# ══════════════════════════════════════════════════════════════════════════
# 分支 (b): 相同指纹二次 build 拒绝
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_second_build_rejected_idempotent():
    await _reset()
    await _seed_active_kb_with_points(tech_category="backhand_topspin", point_count=3)

    async with _sess() as session:
        async with session.begin():
            builder = TechStandardBuilder(session)
            await builder.build_standard("backhand_topspin")

    # 二次 build：相同 active KB + 相同 points → 幂等拒绝
    async with _sess() as session:
        with pytest.raises(AppException) as exc:
            async with session.begin():
                builder = TechStandardBuilder(session)
                await builder.build_standard("backhand_topspin")
        assert exc.value.code == ErrorCode.STANDARD_ALREADY_UP_TO_DATE


# ══════════════════════════════════════════════════════════════════════════
# 分支 (c): 无 active KB
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_active_kb_raises():
    await _reset()

    async with _sess() as session:
        with pytest.raises(AppException) as exc:
            async with session.begin():
                builder = TechStandardBuilder(session)
                await builder.build_standard("serve")
        assert exc.value.code == ErrorCode.NO_ACTIVE_KB_FOR_CATEGORY
