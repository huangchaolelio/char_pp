"""Feature-019 US3 — standards build per-category contract test.

对齐 contracts/standards-build.yaml:
  - 200: 首次 build / 再次 build 对应新版
  - 409 NO_ACTIVE_KB_FOR_CATEGORY
  - 409 STANDARD_ALREADY_UP_TO_DATE
  - 422 VALIDATION_FAILED (缺 tech_category)
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.errors import register_exception_handlers
from src.api.routers.standards import router as std_router
from src.db import session as _db_session
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


@pytest.fixture(autouse=True)
async def _rebind_session_factory():
    """Engine dispose+recreate per test — binds to current event loop."""
    await _db_session.engine.dispose()
    _db_session.engine = _db_session._make_engine()
    _db_session.AsyncSessionFactory.kw["bind"] = _db_session.engine
    yield
    await _db_session.engine.dispose()


def _factory():
    return _db_session.AsyncSessionFactory


@pytest.fixture
def app_std() -> FastAPI:
    _app = FastAPI()
    register_exception_handlers(_app)
    _app.include_router(std_router, prefix="/api/v1")
    return _app


async def _seed_active_kb_with_points(
    *,
    tech_category: str,
    version: int = 1,
    point_count: int = 3,
) -> uuid.UUID:
    """Seed 一个 active KB + N 条合格 expert_tech_points。"""
    async with _factory()() as session:
        # 前置：analysis_task + extraction_job
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
                "VALUES (:jid, :tid, 'success', 'cos://seed', :tc, 'TRAINING', 'extract_kb')"
            ),
            {"jid": job_id, "tid": task_id, "tc": tech_category},
        )
        await session.execute(
            sa.text(
                "INSERT INTO tech_knowledge_bases "
                "(tech_category, version, status, point_count, extraction_job_id, "
                " business_phase, business_step) "
                "VALUES (:tc, :ver, 'active', :pc, :jid, "
                "        'STANDARDIZATION', 'kb_version_activate')"
            ),
            {"tc": tech_category, "ver": version, "pc": point_count, "jid": job_id},
        )

        # 插 N 条合格 points
        for i in range(point_count):
            await session.execute(
                sa.text(
                    "INSERT INTO expert_tech_points "
                    "(id, kb_tech_category, kb_version, action_type, dimension, "
                    " param_min, param_max, param_ideal, unit, extraction_confidence, "
                    " source_video_id, source_type, conflict_flag) "
                    "VALUES (gen_random_uuid(), :tc, :ver, :at, :dim, "
                    "        10.0, 20.0, 15.0, 'deg', 0.85, "
                    "        :tid, 'visual', false)"
                ),
                {"tc": tech_category, "ver": version, "at": tech_category,
                 "dim": f"dim_{i}", "tid": task_id},
            )
        await session.commit()
        return task_id


async def _reset_tables() -> None:
    async with _factory()() as session:
        for tbl in ("tech_standard_points", "tech_standards",
                    "teaching_tips", "expert_tech_points",
                    "tech_knowledge_bases", "extraction_jobs"):
            await session.execute(sa.text(f"DELETE FROM {tbl}"))
        await session.execute(sa.text(
            "DELETE FROM analysis_tasks WHERE task_type='kb_extraction'"
        ))
        await session.commit()


# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_build_200_first_then_idempotent(app_std: FastAPI) -> None:
    await _reset_tables()
    await _seed_active_kb_with_points(tech_category="forehand_attack", version=1)

    async with AsyncClient(transport=ASGITransport(app=app_std), base_url="http://t") as c:
        # 首次 build
        resp = await c.post(
            "/api/v1/standards/build",
            json={"tech_category": "forehand_attack"},
        )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        assert data["mode"] == "single"
        assert data["tech_category"] == "forehand_attack"
        assert data["result"]["result"] == "success"
        assert data["result"]["version"] == 1

        # 二次 build（相同 active KB，相同 points）→ 幂等拒绝
        resp = await c.post(
            "/api/v1/standards/build",
            json={"tech_category": "forehand_attack"},
        )
        assert resp.status_code == 409
        err = assert_error_envelope(resp.json(), code="STANDARD_ALREADY_UP_TO_DATE")
        assert err["details"]["tech_category"] == "forehand_attack"


@pytest.mark.asyncio
async def test_build_409_no_active_kb(app_std: FastAPI) -> None:
    await _reset_tables()  # 没有 active KB

    async with AsyncClient(transport=ASGITransport(app=app_std), base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/standards/build",
            json={"tech_category": "backhand_topspin"},
        )
        assert resp.status_code == 409
        assert_error_envelope(resp.json(), code="NO_ACTIVE_KB_FOR_CATEGORY")


@pytest.mark.asyncio
async def test_build_422_missing_tech_category(app_std: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app_std), base_url="http://t") as c:
        resp = await c.post("/api/v1/standards/build", json={})
        assert resp.status_code == 422
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")


@pytest.mark.asyncio
async def test_build_cross_category_isolation(app_std: FastAPI) -> None:
    """为正手攻球 build 不影响反手拉已存在的 active standard."""
    await _reset_tables()
    # 两个类别都有 active KB
    await _seed_active_kb_with_points(tech_category="forehand_attack", version=1)
    await _seed_active_kb_with_points(tech_category="backhand_topspin", version=1)

    async with AsyncClient(transport=ASGITransport(app=app_std), base_url="http://t") as c:
        # 先 build backhand_topspin
        resp = await c.post(
            "/api/v1/standards/build",
            json={"tech_category": "backhand_topspin"},
        )
        assert resp.status_code == 200
        # 再 build forehand_attack
        resp = await c.post(
            "/api/v1/standards/build",
            json={"tech_category": "forehand_attack"},
        )
        assert resp.status_code == 200

    # 两类别各自 active，互不影响
    async with _factory()() as session:
        rows = await session.execute(sa.text(
            "SELECT tech_category, version, status FROM tech_standards "
            "WHERE status='active' ORDER BY tech_category"
        ))
        actives = rows.all()
        assert len(actives) == 2
        cats = {r[0] for r in actives}
        assert cats == {"backhand_topspin", "forehand_attack"}
