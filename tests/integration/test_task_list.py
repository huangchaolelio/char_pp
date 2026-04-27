"""Integration tests for Feature 012 — task list query (GET /api/v1/tasks)."""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.api.main import app
import src.db.session as _db_session


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    # Dispose and recreate the engine per test to avoid asyncpg
    # "attached to a different loop" errors when each test runs in its own loop.
    await _db_session.engine.dispose()
    _db_session.engine = _db_session._make_engine()
    _db_session.AsyncSessionFactory.kw["bind"] = _db_session.engine

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    await _db_session.engine.dispose()


# ── US1: Basic list and pagination ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_tasks_default_pagination(async_client: AsyncClient) -> None:
    """Default GET /tasks returns page=1, page_size=20 with correct structure."""
    response = await async_client.get("/api/v1/tasks")
    assert response.status_code == 200
    body = response.json()
    # Feature-017：信封化，分页元数据在 meta，业务列表在 data
    assert body["success"] is True
    meta = body["meta"]
    items = body["data"]
    assert meta["page"] == 1
    assert meta["page_size"] == 20
    assert len(items) <= 20
    assert meta["total"] >= 0
    # total_pages 字段已下线，前端按 ceil(total/page_size) 自算


@pytest.mark.asyncio
async def test_list_tasks_custom_pagination(async_client: AsyncClient) -> None:
    """Custom page and page_size are respected."""
    response = await async_client.get("/api/v1/tasks?page=1&page_size=5")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["page"] == 1
    assert body["meta"]["page_size"] == 5
    assert len(body["data"]) <= 5


@pytest.mark.asyncio
async def test_list_tasks_page_size_over_max_rejected(async_client: AsyncClient) -> None:
    """page_size > 100 返回 422 + VALIDATION_FAILED（Feature-017 阶段 5 T054：
    章程 v1.4.0 禁止静默截断，原 `_MAX_PAGE_SIZE` 截断逻辑已下线，改由 Pydantic
    ``Query(le=100)`` 硬约束）."""
    response = await async_client.get("/api/v1/tasks?page_size=999")
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_list_tasks_default_sort_is_created_at_desc(async_client: AsyncClient) -> None:
    """Default sort is by created_at descending (newest first)."""
    response = await async_client.get("/api/v1/tasks?page_size=10")
    assert response.status_code == 200
    items = response.json()["data"]

    if len(items) >= 2:
        for i in range(len(items) - 1):
            assert items[i]["created_at"] >= items[i + 1]["created_at"]


# ── US1: Parameter validation ─────────────────────────────────

@pytest.mark.asyncio
async def test_list_tasks_invalid_status_returns_400(async_client: AsyncClient) -> None:
    """Invalid status value returns 400 with clear error message."""
    response = await async_client.get("/api/v1/tasks?status=invalid_status")
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_ENUM_VALUE"
    assert body["error"]["details"]["field"] == "status"
    assert body["error"]["details"]["value"] == "invalid_status"


@pytest.mark.asyncio
async def test_list_tasks_invalid_task_type_returns_400(async_client: AsyncClient) -> None:
    """Invalid task_type value returns 400."""
    response = await async_client.get("/api/v1/tasks?task_type=unknown_type")
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_ENUM_VALUE"
    assert body["error"]["details"]["field"] == "task_type"

# ── US3: Filtering ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(async_client: AsyncClient) -> None:
    """Filtering by status returns only tasks with that status."""
    response = await async_client.get("/api/v1/tasks?status=processing")
    assert response.status_code == 200
    for item in response.json()["data"]:
        assert item["status"] == "processing"


@pytest.mark.asyncio
@pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types (Alembic 0012 removed these enum values)")
async def test_list_tasks_filter_by_task_type(async_client: AsyncClient) -> None:
    """Filtering by task_type returns only tasks of that type."""
    response = await async_client.get("/api/v1/tasks?task_type=expert_video")
    assert response.status_code == 200
    for item in response.json()["data"]:
        assert item["task_type"] == "expert_video"


@pytest.mark.asyncio
async def test_list_tasks_sort_by_completed_at_nulls_last(async_client: AsyncClient) -> None:
    """Sort by completed_at desc: tasks with completed_at=None appear at the end."""
    response = await async_client.get(
        "/api/v1/tasks?sort_by=completed_at&order=desc&page_size=50"
    )
    assert response.status_code == 200
    items = response.json()["data"]

    if len(items) < 2:
        pytest.skip("Not enough tasks to verify NULLS LAST ordering")

    first_null_idx = next(
        (i for i, item in enumerate(items) if item["completed_at"] is None), None
    )
    if first_null_idx is not None:
        for item in items[first_null_idx:]:
            assert item["completed_at"] is None, (
                "Non-null completed_at found after null — NULLS LAST violated"
            )


# ── US2: Single task detail with summary ─────────────────────────

@pytest.mark.asyncio
async def test_task_detail_summary_is_populated(async_client: AsyncClient) -> None:
    """GET /tasks/{task_id} always includes a non-null summary."""
    list_response = await async_client.get("/api/v1/tasks?page_size=1")
    items = list_response.json()["data"]
    if not items:
        pytest.skip("No tasks available")

    task_id = items[0]["task_id"]
    response = await async_client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200

    envelope = response.json()
    assert envelope["success"] is True
    summary = envelope["data"].get("summary")
    assert summary is not None
    for key in ["tech_point_count", "semantic_segment_count", "motion_analysis_count",
                "deviation_count", "advice_count"]:
        assert isinstance(summary[key], int) and summary[key] >= 0


@pytest.mark.asyncio
async def test_task_detail_soft_deleted_returns_404(async_client: AsyncClient) -> None:
    """Soft-deleted task is not returned by the detail endpoint."""
    response = await async_client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404