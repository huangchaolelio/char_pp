"""Feature-019 T013 — migration 0017 roundtrip + DB-level constraint verification.

覆盖:
- SC-006: upgrade/downgrade 各执行 3 次循环，无错误
- FR-002: partial unique index `uq_tech_kb_active_per_category` 在 DB 层强制
- FR-004: extraction_job_id NOT NULL（INSERT NULL 应抛 IntegrityError）

运行要求：已有 PostgreSQL 本地服务 + 已配置 DATABASE_URL。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings


@pytest.fixture
def alembic_cfg() -> Config:
    return Config("alembic.ini")


@pytest.mark.integration
def test_migration_0017_roundtrip_3_times(alembic_cfg: Config) -> None:
    """SC-006 — upgrade/downgrade 各 3 次无错误。"""
    # 确保从 head 起步（前置条件：测试 runner 已 upgrade head）
    for _ in range(3):
        command.downgrade(alembic_cfg, "-1")  # → 0016
        command.upgrade(alembic_cfg, "head")  # → 0017


@pytest.mark.integration
@pytest.mark.asyncio
async def test_partial_unique_active_per_category() -> None:
    """FR-002 — 同 tech_category 下两条 active 应被 DB 层 partial unique index 拒绝。"""
    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        # 前置：创建 analysis_task + extraction_job 作为 FK 对象
        task_id = uuid.uuid4()
        job_id = uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO analysis_tasks "
                "(id, task_type, video_filename, video_size_bytes, video_storage_uri, "
                " status, submitted_via, business_phase, business_step) "
                "VALUES (:tid, 'kb_extraction', 'test.mp4', 1, 'cos://tmp/test.mp4', "
                "        'success', 'single', 'STANDARDIZATION', 'kb_version_activate')"
            ),
            {"tid": task_id},
        )
        await session.execute(
            sa.text(
                "INSERT INTO extraction_jobs "
                "(id, analysis_task_id, status, cos_object_key, tech_category, "
                " business_phase, business_step, started_at) "
                "VALUES (:jid, :tid, 'success', 'cos://tmp/test.mp4', 'forehand_attack', "
                "        'TRAINING', 'extract_kb', NOW())"
            ),
            {"jid": job_id, "tid": task_id},
        )

        # 第一条 active — 应成功
        await session.execute(
            sa.text(
                "INSERT INTO tech_knowledge_bases "
                "(tech_category, version, status, point_count, extraction_job_id, "
                " business_phase, business_step) "
                "VALUES ('forehand_attack', 1, 'active', 5, :jid, "
                "        'STANDARDIZATION', 'kb_version_activate')"
            ),
            {"jid": job_id},
        )
        await session.flush()

        # 第二条 active 同类别 — 应被 DB 拒绝
        with pytest.raises(IntegrityError):
            await session.execute(
                sa.text(
                    "INSERT INTO tech_knowledge_bases "
                    "(tech_category, version, status, point_count, extraction_job_id, "
                    " business_phase, business_step) "
                    "VALUES ('forehand_attack', 2, 'active', 5, :jid, "
                    "        'STANDARDIZATION', 'kb_version_activate')"
                ),
                {"jid": job_id},
            )
            await session.flush()

        await session.rollback()  # 清理


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extraction_job_id_not_null() -> None:
    """FR-004 — INSERT 一条 extraction_job_id=NULL 的 KB 应抛 IntegrityError。"""
    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                sa.text(
                    "INSERT INTO tech_knowledge_bases "
                    "(tech_category, version, status, point_count, extraction_job_id) "
                    "VALUES ('forehand_loop_fast', 1, 'draft', 0, NULL)"
                )
            )
            await session.flush()

        await session.rollback()
