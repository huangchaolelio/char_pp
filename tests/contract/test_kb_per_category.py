"""Feature-019 US1/US2 contract tests — KB per-category lifecycle.

对齐 contracts/:
  - kb-versions-list.yaml       (T022)
  - kb-version-detail.yaml      (T023)
  - kb-version-approve.yaml     (T014)

测试架构：真实 DB + FastAPI TestClient。每个测试用例启动前清空 tech_knowledge_bases
/ teaching_tips / expert_tech_points，然后 seed 需要的固定数据。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.errors import register_exception_handlers
from src.api.routers.knowledge_base import router as kb_router
from src.db import session as _db_session
from src.db.session import get_db
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


@pytest.fixture(autouse=True)
async def _rebind_session_factory():
    """每个 test 前 dispose 旧 engine 并重建，绑到当前 event loop。

    避免跨 event loop 触发 asyncpg "Event loop is closed"。
    参照 tests/contract/test_task_list_api.py 的 async_client fixture 模式。
    """
    await _db_session.engine.dispose()
    _db_session.engine = _db_session._make_engine()
    _db_session.AsyncSessionFactory.kw["bind"] = _db_session.engine
    yield
    await _db_session.engine.dispose()


def _factory():
    """每次调用时返回当前绑定的 Factory（不在 import 时快照）。"""
    return _db_session.AsyncSessionFactory


@pytest.fixture
def app_kb() -> FastAPI:
    _app = FastAPI()
    register_exception_handlers(_app)
    _app.include_router(kb_router, prefix="/api/v1")
    return _app


async def _reset_kb_tables(session) -> None:
    """清空所有 FK 链路相关表，避免残留污染。"""
    # 顺序敏感：先子表后主表
    await session.execute(sa.text("DELETE FROM teaching_tips"))
    await session.execute(sa.text("DELETE FROM expert_tech_points"))
    await session.execute(sa.text("DELETE FROM tech_knowledge_bases"))
    await session.execute(sa.text("DELETE FROM extraction_jobs"))
    await session.execute(sa.text("DELETE FROM analysis_tasks WHERE task_type='kb_extraction'"))
    await session.commit()


async def _seed_kb_row(
    session,
    *,
    tech_category: str,
    version: int,
    status: str,
    point_count: int = 5,
    extraction_job_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Seed 一条 KB + 对应 extraction_job + analysis_task（FK 链）。"""
    task_id = uuid.uuid4()
    job_id = extraction_job_id or uuid.uuid4()
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
            "VALUES (:tc, :ver, :st, :pc, :jid, 'STANDARDIZATION', 'kb_version_activate')"
        ),
        {"tc": tech_category, "ver": version, "st": status, "pc": point_count, "jid": job_id},
    )
    return job_id


# ══════════════════════════════════════════════════════════════════════════
# T022 — list contract
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_200_filters_and_pagination(app_kb: FastAPI) -> None:
    async with _factory()() as session:
        await _reset_kb_tables(session)
        await _seed_kb_row(session, tech_category="forehand_attack", version=1, status="archived")
        await _seed_kb_row(session, tech_category="forehand_attack", version=2, status="active")
        await _seed_kb_row(session, tech_category="backhand_topspin", version=1, status="draft")
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_kb), base_url="http://t") as c:
        # 全量（3 条）
        resp = await c.get("/api/v1/knowledge-base/versions")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert len(data) == 3
        assert resp.json()["meta"]["total"] == 3

        # 按 status 过滤
        resp = await c.get("/api/v1/knowledge-base/versions?status=active")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert len(data) == 1 and data[0]["status"] == "active"

        # 按 tech_category 过滤
        resp = await c.get("/api/v1/knowledge-base/versions?tech_category=forehand_attack")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert len(data) == 2

        # page_size 越界 → 422（FastAPI 参数校验先返回 422，而非 400；章程允许 422）
        resp = await c.get("/api/v1/knowledge-base/versions?page_size=500")
        assert resp.status_code == 422

        # status 非法值 → 400 INVALID_ENUM_VALUE
        resp = await c.get("/api/v1/knowledge-base/versions?status=pending")
        assert resp.status_code == 400
        assert_error_envelope(resp.json(), code="INVALID_ENUM_VALUE")


# ══════════════════════════════════════════════════════════════════════════
# T023 — detail contract
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_detail_200_and_404(app_kb: FastAPI) -> None:
    async with _factory()() as session:
        await _reset_kb_tables(session)
        await _seed_kb_row(session, tech_category="forehand_attack", version=1, status="draft")
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_kb), base_url="http://t") as c:
        # 存在
        resp = await c.get("/api/v1/knowledge-base/versions/forehand_attack/1")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json())
        assert data["tech_category"] == "forehand_attack"
        assert data["version"] == 1
        assert "dimensions_summary" in data
        assert data["dimensions_summary"]["total_points"] == 0  # 没 seed points

        # 不存在 → 404
        resp = await c.get("/api/v1/knowledge-base/versions/forehand_attack/999")
        assert resp.status_code == 404
        assert_error_envelope(resp.json(), code="KB_VERSION_NOT_FOUND")


# ══════════════════════════════════════════════════════════════════════════
# T014 — approve contract
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_approve_200_and_cross_category_isolation(app_kb: FastAPI) -> None:
    """核心用户故事 US1 — 批准 backhand 不影响 forehand."""
    async with _factory()() as session:
        await _reset_kb_tables(session)
        await _seed_kb_row(session, tech_category="forehand_attack", version=1, status="active")
        await _seed_kb_row(session, tech_category="backhand_topspin", version=1, status="draft")
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_kb), base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/knowledge-base/versions/backhand_topspin/1/approve",
            json={"approved_by": "coach_zhang"},
        )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["new_active"]["tech_category"] == "backhand_topspin"
        assert data["new_active"]["version"] == 1
        assert data["new_active"]["status"] == "active"
        assert data["previous_active_version"] is None  # backhand 首批

    # 跨类别验证：forehand_attack v1 应仍为 active
    async with _factory()() as session:
        row = await session.execute(
            sa.text(
                "SELECT status FROM tech_knowledge_bases "
                "WHERE tech_category='forehand_attack' AND version=1"
            )
        )
        assert row.scalar_one() == "active"


@pytest.mark.asyncio
async def test_approve_empty_points_rejected(app_kb: FastAPI) -> None:
    async with _factory()() as session:
        await _reset_kb_tables(session)
        await _seed_kb_row(session, tech_category="forehand_attack", version=1,
                           status="draft", point_count=0)
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_kb), base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/knowledge-base/versions/forehand_attack/1/approve",
            json={"approved_by": "coach_zhang"},
        )
        assert resp.status_code == 409
        assert_error_envelope(resp.json(), code="KB_EMPTY_POINTS")


@pytest.mark.asyncio
async def test_approve_not_draft_rejected(app_kb: FastAPI) -> None:
    async with _factory()() as session:
        await _reset_kb_tables(session)
        await _seed_kb_row(session, tech_category="forehand_attack", version=1, status="active")
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_kb), base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/knowledge-base/versions/forehand_attack/1/approve",
            json={"approved_by": "coach_zhang"},
        )
        # KB_VERSION_NOT_DRAFT 在 errors.py 中既有状态码 400（章程：已发布错误码禁止改状态）
        assert resp.status_code == 400
        assert_error_envelope(resp.json(), code="KB_VERSION_NOT_DRAFT")


@pytest.mark.asyncio
async def test_approve_not_found(app_kb: FastAPI) -> None:
    async with _factory()() as session:
        await _reset_kb_tables(session)
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_kb), base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/knowledge-base/versions/forehand_attack/999/approve",
            json={"approved_by": "coach_zhang"},
        )
        assert resp.status_code == 404
        assert_error_envelope(resp.json(), code="KB_VERSION_NOT_FOUND")


@pytest.mark.asyncio
async def test_approve_missing_approved_by(app_kb: FastAPI) -> None:
    async with _factory()() as session:
        await _reset_kb_tables(session)
        await _seed_kb_row(session, tech_category="forehand_attack", version=1, status="draft")
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_kb), base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/knowledge-base/versions/forehand_attack/1/approve",
            json={},
        )
        assert resp.status_code == 422
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")
