"""API contract tests for /api/v1/standards endpoints.

Verifies response structure, HTTP status codes, and error formats
for the tech standards API without hitting a real database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport

from src.api.main import app
from src.db.session import get_db


def _db_override(mock_session):
    async def _get_mock_db():
        yield mock_session

    # Feature-019: 路由层 `async with session.begin():` 打开事务
    # mock 的 AsyncMock 默认不实现 __aenter__/__aexit__，手动注入
    async def _begin():
        class _Ctx:
            async def __aenter__(self_inner):
                return mock_session
            async def __aexit__(self_inner, exc_type, exc, tb):
                return None
        return _Ctx()

    mock_session.begin = MagicMock(side_effect=_begin)
    # 并做一个当作上下文直接返回 _Ctx 的适配（FastAPI router 写的是 `async with db.begin():`）
    _ctx_holder = {}
    class _Ctx:
        async def __aenter__(self_inner):
            return mock_session
        async def __aexit__(self_inner, exc_type, exc, tb):
            return None
    mock_session.begin = MagicMock(return_value=_Ctx())

    app.dependency_overrides[get_db] = _get_mock_db
    return mock_session


def _clear_overrides():
    app.dependency_overrides.clear()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/v1/standards/build — single tech
# ---------------------------------------------------------------------------

class TestBuildSingleTechContract:
    """POST /api/v1/standards/build with tech_category."""

    async def test_response_structure(self, client):
        mock_session = AsyncMock()
        _db_override(mock_session)

        from src.services.tech_standard_builder import BuildResult

        mock_result = BuildResult(
            tech_category="forehand_topspin",
            result="success",
            standard_id=1,
            version=1,
            dimension_count=3,
            coach_count=2,
        )

        with patch(
            "src.api.routers.standards.TechStandardBuilder"
        ) as MockBuilder:
            instance = MockBuilder.return_value
            instance.build_standard = AsyncMock(return_value=mock_result)

            resp = await client.post(
                "/api/v1/standards/build",
                json={"tech_category": "forehand_topspin"},
            )

        _clear_overrides()
        assert resp.status_code == 200
        envelope = resp.json()
        # Feature-017：信封化，业务载荷在 data 字段
        assert envelope["success"] is True
        data = envelope["data"]
        assert "task_id" in data
        assert "mode" in data
        assert data["mode"] == "single"
        assert "tech_category" in data
        assert data["tech_category"] == "forehand_topspin"
        assert "result" in data

    async def test_invalid_tech_category_returns_422(self, client):
        resp = await client.post(
            "/api/v1/standards/build",
            json={"tech_category": "invalid_tech_xyz"},
        )
        assert resp.status_code == 422
        body = resp.json()
        # Feature-017：统一错误信封，顺便验证 VALIDATION_FAILED code
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# POST /api/v1/standards/build — batch 模式已删除（Feature-019 FR-015: tech_category 必填）
# ---------------------------------------------------------------------------
# 原 TestBuildBatchContract 类测试的 `mode=batch` 经已不存在：路由层现要求
# `tech_category` 为必填，缺失直接返回 422 VALIDATION_FAILED。新语义的合约测试
# 见 tests/contract/test_standards_build_per_category.py::test_build_422_missing_tech_category。


# ---------------------------------------------------------------------------
# GET /api/v1/standards/{tech_category} — found
# ---------------------------------------------------------------------------

class TestGetStandardContract:
    """GET /api/v1/standards/{tech_category}."""

    async def test_200_response_structure(self, client):
        mock_session = AsyncMock()
        _db_override(mock_session)

        from src.models.tech_standard import TechStandard, TechStandardPoint

        mock_point = MagicMock(spec=TechStandardPoint)
        mock_point.dimension = "elbow_angle"
        mock_point.ideal = 110.0
        mock_point.min = 95.0
        mock_point.max = 125.0
        mock_point.unit = "°"
        mock_point.sample_count = 5
        mock_point.coach_count = 3

        mock_standard = MagicMock(spec=TechStandard)
        mock_standard.id = 42
        mock_standard.tech_category = "forehand_topspin"
        mock_standard.version = 1
        mock_standard.status = "active"
        mock_standard.source_quality = "multi_source"
        mock_standard.coach_count = 3
        mock_standard.point_count = 5
        mock_standard.built_at = datetime(2026, 4, 22)
        mock_standard.points = [mock_point]

        with patch(
            "src.api.routers.standards.get_active_standard",
            new=AsyncMock(return_value=mock_standard),
        ):
            resp = await client.get("/api/v1/standards/forehand_topspin")

        _clear_overrides()
        assert resp.status_code == 200
        envelope = resp.json()
        assert envelope["success"] is True
        data = envelope["data"]
        assert data["tech_category"] == "forehand_topspin"
        assert "standard_id" in data
        assert "version" in data
        assert "source_quality" in data
        assert "coach_count" in data
        assert "point_count" in data
        assert "built_at" in data
        assert "dimensions" in data
        assert len(data["dimensions"]) == 1
        dim = data["dimensions"][0]
        assert "dimension" in dim
        assert "ideal" in dim
        assert "min" in dim
        assert "max" in dim
        assert "unit" in dim
        assert "sample_count" in dim
        assert "coach_count" in dim

    async def test_404_response_structure(self, client):
        with patch(
            "src.api.routers.standards.get_active_standard",
            new=AsyncMock(return_value=None),
        ):
            resp = await client.get("/api/v1/standards/forehand_topspin")

        assert resp.status_code == 404
        body = resp.json()
        # Feature-017：错误信封 {success:false, error:{code,message,details}}
        assert body["success"] is False
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["error"]["details"]["tech_category"] == "forehand_topspin"


# ---------------------------------------------------------------------------
# GET /api/v1/standards — list
# ---------------------------------------------------------------------------

class TestListStandardsContract:
    """GET /api/v1/standards."""

    async def test_list_response_structure(self, client):
        mock_session = AsyncMock()
        _db_override(mock_session)

        from src.models.tech_standard import TechStandard

        mock_std = MagicMock(spec=TechStandard)
        mock_std.id = 1
        mock_std.tech_category = "forehand_topspin"
        mock_std.version = 1
        mock_std.source_quality = "multi_source"
        mock_std.coach_count = 3
        mock_std.built_at = datetime(2026, 4, 22)
        mock_std.points = []

        with patch(
            "src.api.routers.standards.list_active_standards",
            new=AsyncMock(return_value=[mock_std]),
        ):
            resp = await client.get("/api/v1/standards")

        _clear_overrides()
        assert resp.status_code == 200
        envelope = resp.json()
        assert envelope["success"] is True
        data = envelope["data"]
        assert "standards" in data
        assert "total" in data
        assert "missing_categories" in data
        if data["standards"]:
            item = data["standards"][0]
            assert "tech_category" in item
            assert "standard_id" in item
            assert "version" in item
            assert "source_quality" in item
            assert "coach_count" in item
            assert "dimension_count" in item
            assert "built_at" in item
