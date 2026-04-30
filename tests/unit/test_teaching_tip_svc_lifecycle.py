"""Feature-019 US4 T032 — teaching_tip_svc.relink_on_kb_approve 单元测试.

对齐 data-model.md § 实体 3 生命周期联动（2 步 UPDATE）+ spec.md FR-024.

覆盖 4 条分支:
  (a) 该类别首批 KB approve（old_version=None）→ 只执行激活，无归档
  (b) 同类别 KB 切换（old→new）→ 归档旧 auto + 激活新 draft
  (c) human tips 不参与批量归档（FR-024 核心断言）
  (d) 空输入（该 (tc, ver) 下无 tips）→ 返回 {archived:0, activated:0}
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.db import session as _db_session
from src.services import teaching_tip_svc


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


async def _seed_kb(tech_category: str, version: int, status: str = "draft") -> None:
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
                "VALUES (:tc, :ver, :st, 1, :jid, "
                "        'STANDARDIZATION', 'kb_version_activate')"
            ),
            {"tc": tech_category, "ver": version, "st": status, "jid": jid},
        )
        await session.commit()


async def _seed_tip(
    *,
    tech_category: str,
    kb_version: int,
    status: str,
    source_type: str = "auto",
) -> None:
    async with _sess() as session:
        await session.execute(
            sa.text(
                "INSERT INTO teaching_tips "
                "(id, tech_category, kb_tech_category, kb_version, "
                " status, tech_phase, tip_text, confidence, source_type) "
                "VALUES (gen_random_uuid(), :tc, :tc, :ver, :st, 'contact', "
                "        :text, 0.9, :src)"
            ),
            {
                "tc": tech_category,
                "ver": kb_version,
                "st": status,
                "src": source_type,
                "text": f"{status}/{source_type}/v{kb_version}",
            },
        )
        await session.commit()


# ══════════════════════════════════════════════════════════════════════════
# 分支 (a): 首批 approve（old_version=None）
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_first_approve_only_activates():
    await _reset()
    await _seed_kb("forehand_attack", 1, "draft")
    await _seed_tip(tech_category="forehand_attack", kb_version=1, status="draft", source_type="auto")
    await _seed_tip(tech_category="forehand_attack", kb_version=1, status="draft", source_type="human")

    async with _sess() as session:
        stats = await teaching_tip_svc.relink_on_kb_approve(
            session,
            tech_category="forehand_attack",
            old_version=None,
            new_version=1,
        )
        await session.commit()

    # 首批 approve：archived=0（无前任），activated=2（draft auto + draft human 一视同仁）
    assert stats == {"archived_count": 0, "activated_count": 2}

    # 验证 DB 状态
    async with _sess() as session:
        rows = (await session.execute(
            sa.text(
                "SELECT status, source_type FROM teaching_tips "
                "WHERE tech_category='forehand_attack'"
            )
        )).all()
        assert len(rows) == 2
        assert all(r[0] == "active" for r in rows)


# ══════════════════════════════════════════════════════════════════════════
# 分支 (b): 同类别 KB 切换 — 归档旧 auto + 激活新 draft
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_switch_archives_old_auto_and_activates_new_draft():
    await _reset()
    await _seed_kb("backhand_topspin", 1, "active")
    await _seed_kb("backhand_topspin", 2, "draft")
    # 旧 KB 下 2 条 active auto + 1 条 active human
    await _seed_tip(tech_category="backhand_topspin", kb_version=1, status="active", source_type="auto")
    await _seed_tip(tech_category="backhand_topspin", kb_version=1, status="active", source_type="auto")
    await _seed_tip(tech_category="backhand_topspin", kb_version=1, status="active", source_type="human")
    # 新 KB 下 3 条 draft auto
    for _ in range(3):
        await _seed_tip(tech_category="backhand_topspin", kb_version=2, status="draft", source_type="auto")

    async with _sess() as session:
        stats = await teaching_tip_svc.relink_on_kb_approve(
            session,
            tech_category="backhand_topspin",
            old_version=1,
            new_version=2,
        )
        await session.commit()

    # 归档 2 条旧 auto（human 不动 → FR-024）；激活 3 条新 draft
    assert stats == {"archived_count": 2, "activated_count": 3}


# ══════════════════════════════════════════════════════════════════════════
# 分支 (c): human tips 在批量归档中被保留（FR-024 核心）
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_human_tips_not_archived_batch():
    await _reset()
    await _seed_kb("serve", 1, "active")
    await _seed_kb("serve", 2, "draft")
    # 旧 KB 下只有 human tip
    await _seed_tip(tech_category="serve", kb_version=1, status="active", source_type="human")

    async with _sess() as session:
        stats = await teaching_tip_svc.relink_on_kb_approve(
            session,
            tech_category="serve",
            old_version=1,
            new_version=2,
        )
        await session.commit()

    # archived_count=0：human tip 被保留
    assert stats["archived_count"] == 0

    # 验证 human tip 仍为 active
    async with _sess() as session:
        row = (await session.execute(
            sa.text(
                "SELECT status, source_type FROM teaching_tips "
                "WHERE tech_category='serve' AND kb_version=1"
            )
        )).one()
        assert row[0] == "active"
        assert row[1] == "human"


# ══════════════════════════════════════════════════════════════════════════
# 分支 (d): 空输入 — 返回 {0, 0}
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_empty_input_returns_zero():
    await _reset()
    await _seed_kb("defense", 1, "draft")  # 仅 KB，无 tips

    async with _sess() as session:
        stats = await teaching_tip_svc.relink_on_kb_approve(
            session,
            tech_category="defense",
            old_version=None,
            new_version=1,
        )
        await session.commit()

    assert stats == {"archived_count": 0, "activated_count": 0}
