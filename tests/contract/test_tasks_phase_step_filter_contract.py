"""Feature-018 T018 — GET /api/v1/tasks?business_phase / business_step 合约测试.

重点覆盖：参数校验与组合矛盾校验的信封契约（DB 层面使用 mock）。

- ?business_phase=TRAINING ⇒ 200 + 合法信封（返回空列表）
- ?business_step=extract_kb ⇒ 200
- ?business_phase=INFERENCE&task_type=kb_extraction ⇒ 400 INVALID_PHASE_STEP_COMBO
- ?business_phase=INVALID ⇒ 400 INVALID_ENUM_VALUE
- ?business_step=bad_step ⇒ 400 INVALID_ENUM_VALUE
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.errors import register_exception_handlers
from src.api.routers.tasks import router as tasks_router
from src.db.session import get_db
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


@pytest.fixture
def app_with_tasks() -> FastAPI:
    _app = FastAPI()
    register_exception_handlers(_app)
    _app.include_router(tasks_router, prefix="/api/v1")
    return _app


@pytest.fixture
def override_db_empty(app_with_tasks):
    async def _fake_db():
        session = MagicMock()
        # 让 count 返回 0
        scalar_res = MagicMock()
        scalar_res.scalar_one = MagicMock(return_value=0)
        rows_res = MagicMock()
        rows_res.all = MagicMock(return_value=[])

        async def _exec(stmt, *args, **kwargs):
            s = str(stmt)
            if "count" in s.lower():
                return scalar_res
            return rows_res

        session.execute = AsyncMock(side_effect=_exec)
        yield session

    app_with_tasks.dependency_overrides[get_db] = _fake_db
    yield
    app_with_tasks.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_filter_by_business_phase_training(app_with_tasks, override_db_empty):
    async with AsyncClient(transport=ASGITransport(app=app_with_tasks), base_url="http://t") as c:
        resp = await c.get("/api/v1/tasks?business_phase=TRAINING")
    assert resp.status_code == 200
    assert_success_envelope(resp.json(), expect_meta=True)


@pytest.mark.asyncio
async def test_filter_by_business_step_extract_kb(app_with_tasks, override_db_empty):
    async with AsyncClient(transport=ASGITransport(app=app_with_tasks), base_url="http://t") as c:
        resp = await c.get("/api/v1/tasks?business_step=extract_kb")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_phase_step_task_type_combo_conflict(app_with_tasks, override_db_empty):
    """INFERENCE 阶段 + kb_extraction 任务类型 ⇒ 400 INVALID_PHASE_STEP_COMBO."""
    async with AsyncClient(transport=ASGITransport(app=app_with_tasks), base_url="http://t") as c:
        resp = await c.get("/api/v1/tasks?business_phase=INFERENCE&task_type=kb_extraction")
    assert resp.status_code == 400
    body = resp.json()
    err = assert_error_envelope(body, code="INVALID_PHASE_STEP_COMBO")
    assert err.get("details", {}).get("phase") == "INFERENCE"
    assert err.get("details", {}).get("task_type") == "kb_extraction"


@pytest.mark.asyncio
async def test_invalid_phase_enum(app_with_tasks, override_db_empty):
    async with AsyncClient(transport=ASGITransport(app=app_with_tasks), base_url="http://t") as c:
        resp = await c.get("/api/v1/tasks?business_phase=INVALID_FOO")
    assert resp.status_code == 400
    assert_error_envelope(resp.json(), code="INVALID_ENUM_VALUE")


@pytest.mark.asyncio
async def test_invalid_step_value(app_with_tasks, override_db_empty):
    async with AsyncClient(transport=ASGITransport(app=app_with_tasks), base_url="http://t") as c:
        resp = await c.get("/api/v1/tasks?business_step=bad_step")
    assert resp.status_code == 400
    assert_error_envelope(resp.json(), code="INVALID_ENUM_VALUE")
