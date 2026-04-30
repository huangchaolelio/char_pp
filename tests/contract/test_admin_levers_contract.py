"""Feature-018 T040 — GET /api/v1/admin/levers 合约测试.

覆盖：
- 200 成功：三类分组 + 敏感键 is_configured / 非敏感键 current_value
- 401 ADMIN_TOKEN_INVALID：缺 token 或 token 不匹配
- 500 ADMIN_TOKEN_NOT_CONFIGURED：服务端未配置 token
- ?phase= 过滤
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.errors import register_exception_handlers
from src.api.routers.admin import router as admin_router
from src.db.session import get_db
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


@pytest.fixture
def app_with_admin() -> FastAPI:
    _app = FastAPI()
    register_exception_handlers(_app)
    _app.include_router(admin_router, prefix="/api/v1")
    return _app


@pytest.fixture
def override_db(app_with_admin):
    from unittest.mock import AsyncMock, MagicMock

    async def _fake_db():
        session = MagicMock()
        res = MagicMock()
        res.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        session.execute = AsyncMock(return_value=res)
        yield session

    app_with_admin.dependency_overrides[get_db] = _fake_db
    yield
    app_with_admin.dependency_overrides.clear()


@pytest.fixture
def admin_token(monkeypatch):
    monkeypatch.setenv("ADMIN_RESET_TOKEN", "test-admin-token")
    from src.config import get_settings
    get_settings.cache_clear()
    return "test-admin-token"


@pytest.fixture
def reset_levers_singleton():
    """避免单例跨测试污染（敏感键状态会残留）."""
    import src.api.routers.admin as admin_mod
    admin_mod._LEVERS_SERVICE = None
    yield
    admin_mod._LEVERS_SERVICE = None


@pytest.mark.asyncio
async def test_200_three_groups_returned(
    app_with_admin, override_db, admin_token, reset_levers_singleton, monkeypatch,
):
    monkeypatch.setenv("POSE_BACKEND", "auto")
    monkeypatch.setenv("VENUS_TOKEN", "sk-xxx")

    async with AsyncClient(transport=ASGITransport(app=app_with_admin), base_url="http://t") as c:
        resp = await c.get(
            "/api/v1/admin/levers",
            headers={"X-Admin-Token": admin_token},
        )
    assert resp.status_code == 200
    body = resp.json()
    data = assert_success_envelope(body)
    assert "runtime_params" in data
    assert "algorithm_models" in data
    assert "rules_prompts" in data

    # 敏感键 VENUS_TOKEN：is_configured=True，无 current_value
    venus = next((e for e in data["algorithm_models"] if e["key"] == "VENUS_TOKEN"), None)
    assert venus is not None
    assert venus.get("is_configured") is True
    # 关键白盒：current_value 必须为 None
    assert venus.get("current_value") is None

    # 非敏感键 POSE_BACKEND
    pose = next((e for e in data["algorithm_models"] if e["key"] == "POSE_BACKEND"), None)
    assert pose is not None
    assert pose.get("current_value") == "auto"


@pytest.mark.asyncio
async def test_401_missing_token(app_with_admin, override_db, admin_token, reset_levers_singleton):
    async with AsyncClient(transport=ASGITransport(app=app_with_admin), base_url="http://t") as c:
        resp = await c.get("/api/v1/admin/levers")
    assert resp.status_code == 401
    body = resp.json()
    assert_error_envelope(body, code="ADMIN_TOKEN_INVALID")


@pytest.mark.asyncio
async def test_401_wrong_token(app_with_admin, override_db, admin_token, reset_levers_singleton):
    async with AsyncClient(transport=ASGITransport(app=app_with_admin), base_url="http://t") as c:
        resp = await c.get(
            "/api/v1/admin/levers",
            headers={"X-Admin-Token": "wrong-token"},
        )
    assert resp.status_code == 401
    assert_error_envelope(resp.json(), code="ADMIN_TOKEN_INVALID")


@pytest.mark.asyncio
async def test_500_admin_token_not_configured(
    app_with_admin, override_db, reset_levers_singleton, monkeypatch,
):
    monkeypatch.setenv("ADMIN_RESET_TOKEN", "")
    from src.config import get_settings
    get_settings.cache_clear()

    async with AsyncClient(transport=ASGITransport(app=app_with_admin), base_url="http://t") as c:
        resp = await c.get(
            "/api/v1/admin/levers",
            headers={"X-Admin-Token": "any"},
        )
    assert resp.status_code == 500
    assert_error_envelope(resp.json(), code="ADMIN_TOKEN_NOT_CONFIGURED")


@pytest.mark.asyncio
async def test_phase_filter(
    app_with_admin, override_db, admin_token, reset_levers_singleton, monkeypatch,
):
    monkeypatch.setenv("POSE_BACKEND", "auto")
    async with AsyncClient(transport=ASGITransport(app=app_with_admin), base_url="http://t") as c:
        resp = await c.get(
            "/api/v1/admin/levers?phase=INFERENCE",
            headers={"X-Admin-Token": admin_token},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # kb_extraction.concurrency 仅在 TRAINING；INFERENCE 下应被过滤
    keys_runtime = {e["key"] for e in data["runtime_params"]}
    assert "task_channel_configs.kb_extraction.concurrency" not in keys_runtime
    # POSE_BACKEND 影响 TRAINING+INFERENCE，INFERENCE 应含
    keys_alg = {e["key"] for e in data["algorithm_models"]}
    assert "POSE_BACKEND" in keys_alg


@pytest.mark.asyncio
async def test_400_invalid_phase(
    app_with_admin, override_db, admin_token, reset_levers_singleton,
):
    async with AsyncClient(transport=ASGITransport(app=app_with_admin), base_url="http://t") as c:
        resp = await c.get(
            "/api/v1/admin/levers?phase=BAD",
            headers={"X-Admin-Token": admin_token},
        )
    assert resp.status_code == 400
    assert_error_envelope(resp.json(), code="INVALID_ENUM_VALUE")
