"""Feature-019 US4 T034a — GET /teaching-tips 默认仅返 active 合约测试.

对齐 spec.md FR-023 / FR-024：
  - 无 include_status → 仅返 status='active'（含 human 也算 active）
  - ?include_status=draft,archived → 扩展返回
  - 非法 status 值 → 400 INVALID_ENUM_VALUE
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.errors import register_exception_handlers
from src.api.routers.teaching_tips import router as tips_router
from src.db import session as _db_session
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


@pytest.fixture(autouse=True)
async def _rebind_session_factory():
    """Engine dispose+recreate per test，确保绑定当前 event loop。"""
    await _db_session.engine.dispose()
    _db_session.engine = _db_session._make_engine()
    _db_session.AsyncSessionFactory.kw["bind"] = _db_session.engine
    yield
    await _db_session.engine.dispose()


def _factory():
    return _db_session.AsyncSessionFactory


@pytest.fixture
def app_tips() -> FastAPI:
    _app = FastAPI()
    register_exception_handlers(_app)
    _app.include_router(tips_router, prefix="/api/v1")
    return _app


async def _reset_tables() -> None:
    async with _factory()() as session:
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


async def _seed_kb_and_tips(
    *,
    tech_category: str,
    statuses: list[tuple[str, str]],  # list of (status, source_type)
) -> tuple[str, int]:
    """Seed 一个 KB + 对应若干 tips（statuses[i] 定义第 i 条 tip 的 status + source_type）."""
    async with _factory()() as session:
        task_id = uuid.uuid4()
        job_id = uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO analysis_tasks "
                "(id, task_type, video_filename, video_size_bytes, video_storage_uri, "
                " status, submitted_via, business_phase, business_step) "
                "VALUES (:tid, 'kb_extraction', 'seed.mp4', 1, 'cos://seed', "
                "        'success', 'single', 'TRAINING', 'extract_kb')"
            ),
            {"tid": task_id},
        )
        await session.execute(
            sa.text(
                "INSERT INTO extraction_jobs "
                "(id, analysis_task_id, status, cos_object_key, tech_category, "
                " business_phase, business_step) "
                "VALUES (:jid, :tid, 'success', 'cos://seed', :tc, "
                "        'TRAINING', 'extract_kb')"
            ),
            {"jid": job_id, "tid": task_id, "tc": tech_category},
        )
        await session.execute(
            sa.text(
                "INSERT INTO tech_knowledge_bases "
                "(tech_category, version, status, point_count, extraction_job_id, "
                " business_phase, business_step) "
                "VALUES (:tc, 1, 'active', 3, :jid, "
                "        'STANDARDIZATION', 'kb_version_activate')"
            ),
            {"tc": tech_category, "jid": job_id},
        )
        # Seed tips（每条独立文本以便校验）
        for i, (status, src) in enumerate(statuses):
            await session.execute(
                sa.text(
                    "INSERT INTO teaching_tips "
                    "(id, task_id, tech_category, kb_tech_category, kb_version, "
                    " status, tech_phase, tip_text, confidence, source_type) "
                    "VALUES (gen_random_uuid(), :tid, :tc, :tc, 1, "
                    "        :st, 'contact', :text, 0.9, :src)"
                ),
                {
                    "tid": task_id,
                    "tc": tech_category,
                    "st": status,
                    "src": src,
                    "text": f"tip #{i} ({status}/{src})",
                },
            )
        await session.commit()
    return tech_category, 1


# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_default_returns_only_active(app_tips: FastAPI) -> None:
    await _reset_tables()
    await _seed_kb_and_tips(
        tech_category="forehand_attack",
        statuses=[("active", "auto"), ("draft", "auto"), ("archived", "human")],
    )

    async with AsyncClient(transport=ASGITransport(app=app_tips), base_url="http://t") as c:
        resp = await c.get("/api/v1/teaching-tips")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert len(data) == 1
        assert data[0]["status"] == "active"
        assert data[0]["tech_category"] == "forehand_attack"
        # 响应字段新增断言
        assert "kb_tech_category" in data[0]
        assert "kb_version" in data[0]
        assert data[0]["kb_version"] == 1


@pytest.mark.asyncio
async def test_include_status_widens_filter(app_tips: FastAPI) -> None:
    await _reset_tables()
    await _seed_kb_and_tips(
        tech_category="backhand_topspin",
        statuses=[("active", "auto"), ("draft", "auto"), ("archived", "human")],
    )

    async with AsyncClient(transport=ASGITransport(app=app_tips), base_url="http://t") as c:
        # 放宽到 draft+archived+active 3 个
        resp = await c.get("/api/v1/teaching-tips?include_status=draft,archived,active")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert len(data) == 3
        got = {d["status"] for d in data}
        assert got == {"active", "draft", "archived"}

        # 仅 archived
        resp = await c.get("/api/v1/teaching-tips?include_status=archived")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert len(data) == 1
        assert data[0]["status"] == "archived"


@pytest.mark.asyncio
async def test_invalid_status_returns_400(app_tips: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app_tips), base_url="http://t") as c:
        resp = await c.get("/api/v1/teaching-tips?include_status=pending")
        assert resp.status_code == 400
        err = assert_error_envelope(resp.json(), code="INVALID_ENUM_VALUE")
        assert err["details"]["field"] == "include_status"
        assert "allowed" in err["details"]


@pytest.mark.asyncio
async def test_filter_by_kb_composite_key(app_tips: FastAPI) -> None:
    """FR-024: 支持按 (kb_tech_category, kb_version) 复合键精确过滤."""
    await _reset_tables()
    await _seed_kb_and_tips(
        tech_category="forehand_attack",
        statuses=[("active", "auto"), ("active", "human")],
    )
    await _seed_kb_and_tips(
        tech_category="serve",
        statuses=[("active", "auto")],
    )

    async with AsyncClient(transport=ASGITransport(app=app_tips), base_url="http://t") as c:
        resp = await c.get(
            "/api/v1/teaching-tips?kb_tech_category=forehand_attack&kb_version=1"
        )
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert len(data) == 2
        assert all(d["kb_tech_category"] == "forehand_attack" for d in data)


@pytest.mark.asyncio
async def test_human_source_preserved_across_status_filter(app_tips: FastAPI) -> None:
    """FR-024: human 来源的 archived tip 在 include_status=archived 下仍应可见."""
    await _reset_tables()
    await _seed_kb_and_tips(
        tech_category="forehand_attack",
        statuses=[("archived", "human"), ("archived", "auto")],
    )

    async with AsyncClient(transport=ASGITransport(app=app_tips), base_url="http://t") as c:
        resp = await c.get("/api/v1/teaching-tips?include_status=archived")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert len(data) == 2
        sources = {d["source_type"] for d in data}
        assert sources == {"human", "auto"}
