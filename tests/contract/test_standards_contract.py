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
        data = resp.json()
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
        data = resp.json()
        assert "error" in data or "detail" in data


# ---------------------------------------------------------------------------
# POST /api/v1/standards/build — batch (no tech_category)
# ---------------------------------------------------------------------------

class TestBuildBatchContract:
    """POST /api/v1/standards/build without tech_category triggers batch."""

    async def test_batch_response_structure(self, client):
        mock_session = AsyncMock()
        _db_override(mock_session)

        from src.services.tech_standard_builder import BatchBuildResult, BuildResult

        mock_results = [
            BuildResult(
                tech_category="forehand_topspin",
                result="success",
                standard_id=1,
                version=1,
                dimension_count=3,
                coach_count=2,
            ),
            BuildResult(
                tech_category="backhand_push",
                result="skipped",
                reason="no_valid_points",
            ),
        ]
        mock_batch = BatchBuildResult(results=mock_results)

        with patch(
            "src.api.routers.standards.TechStandardBuilder"
        ) as MockBuilder:
            instance = MockBuilder.return_value
            instance.build_all = AsyncMock(return_value=mock_batch)

            resp = await client.post("/api/v1/standards/build", json={})

        _clear_overrides()
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert data["mode"] == "batch"
        assert "results" in data
        assert "summary" in data
        summary = data["summary"]
        assert "success_count" in summary
        assert "skipped_count" in summary
        assert "failed_count" in summary


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
        mock_standard.built_at = datetime(2026, 4, 22, tzinfo=timezone.utc)
        mock_standard.points = [mock_point]

        with patch(
            "src.api.routers.standards.get_active_standard",
            new=AsyncMock(return_value=mock_standard),
        ):
            resp = await client.get("/api/v1/standards/forehand_topspin")

        _clear_overrides()
        assert resp.status_code == 200
        data = resp.json()
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
        data = resp.json()
        # FastAPI wraps HTTPException detail under top-level "detail" key
        assert "detail" in data
        inner = data["detail"]
        assert "error" in inner
        assert "detail" in inner


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
        mock_std.built_at = datetime(2026, 4, 22, tzinfo=timezone.utc)
        mock_std.points = []

        with patch(
            "src.api.routers.standards.list_active_standards",
            new=AsyncMock(return_value=[mock_std]),
        ):
            resp = await client.get("/api/v1/standards")

        _clear_overrides()
        assert resp.status_code == 200
        data = resp.json()
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
