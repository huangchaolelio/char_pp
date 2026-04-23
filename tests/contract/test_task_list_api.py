"""Contract tests for GET /api/v1/tasks — Feature 012 task list query."""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.api.main import app
import src.db.session as _db_session


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    # Dispose and recreate the engine so each test gets a fresh connection
    # pool bound to the current event loop (avoids asyncpg "different loop" errors).
    await _db_session.engine.dispose()
    _db_session.engine = _db_session._make_engine()
    _db_session.AsyncSessionFactory.kw["bind"] = _db_session.engine

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    await _db_session.engine.dispose()


# ── US1: GET /tasks list endpoint contracts ───────────────────────────────────

@pytest.mark.asyncio
async def test_task_list_response_structure(async_client: AsyncClient) -> None:
    """GET /tasks returns a valid paginated structure with all required fields."""
    response = await async_client.get("/api/v1/tasks")
    assert response.status_code == 200
    data = response.json()

    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "total_pages" in data

    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)
    assert isinstance(data["page"], int)
    assert isinstance(data["page_size"], int)
    assert isinstance(data["total_pages"], int)
    assert data["page"] == 1
    assert data["page_size"] == 20


@pytest.mark.asyncio
async def test_task_list_item_fields(async_client: AsyncClient) -> None:
    """Each item in the list contains the required fields."""
    response = await async_client.get("/api/v1/tasks")
    assert response.status_code == 200
    data = response.json()

    for item in data["items"]:
        assert "task_id" in item
        assert "task_type" in item
        assert "status" in item
        assert "video_filename" in item
        assert "video_storage_uri" in item
        assert "created_at" in item


@pytest.mark.asyncio
async def test_task_list_empty_result(async_client: AsyncClient) -> None:
    """GET /tasks with no matching records returns empty list without error."""
    response = await async_client.get("/api/v1/tasks?status=rejected&page=999")
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)
    assert data["total"] >= 0
    assert data["page"] == 999


# ── US2: GET /tasks/{task_id} extended with summary field ────────────────────

@pytest.mark.asyncio
async def test_task_detail_includes_summary(async_client: AsyncClient) -> None:
    """GET /tasks/{task_id} response includes summary field with 6 sub-fields."""
    list_response = await async_client.get("/api/v1/tasks?page_size=1")
    assert list_response.status_code == 200
    items = list_response.json()["items"]

    if not items:
        pytest.skip("No tasks in DB — skipping summary contract test")

    task_id = items[0]["task_id"]
    response = await async_client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()

    assert "summary" in data
    summary = data["summary"]
    assert summary is not None

    for field in ["tech_point_count", "has_transcript", "semantic_segment_count",
                  "motion_analysis_count", "deviation_count", "advice_count"]:
        assert field in summary

    assert isinstance(summary["tech_point_count"], int)
    assert isinstance(summary["has_transcript"], bool)
    assert isinstance(summary["semantic_segment_count"], int)
    assert isinstance(summary["motion_analysis_count"], int)
    assert isinstance(summary["deviation_count"], int)
    assert isinstance(summary["advice_count"], int)


@pytest.mark.asyncio
async def test_task_detail_not_found_returns_404(async_client: AsyncClient) -> None:
    """GET /tasks/{nonexistent_id} returns 404, not 500."""
    response = await async_client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert "detail" in response.json()
