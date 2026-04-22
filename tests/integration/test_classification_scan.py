"""Integration tests for classification scan API (Feature 008 — T018).

Tests:
  1. POST /scan triggers task and returns 202 + task_id
  2. GET /scan/{task_id} returns status (pending/running/success)
  3. GET /classifications returns list with correct structure
  4. GET /classifications?kb_extracted=false returns only unextracted records
  5. GET /classifications/summary returns aggregated data
  6. PATCH /classifications/{id} updates tech_category and sets source=manual
  7. Invalid scan_mode returns 400
  8. Invalid tech_category in PATCH returns 400
  9. Non-existent classification_id in PATCH returns 404
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.db.session import Base, get_db
from src.models.coach_video_classification import CoachVideoClassification


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_DB_URL = "postgresql+asyncpg://postgres:password@localhost:5432/coaching_db"


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a transaction-wrapped session that is rolled back after each test."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            yield session
            await session.rollback()

    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client with DB session override."""

    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def sample_record(db_session: AsyncSession) -> CoachVideoClassification:
    """Insert a sample classification record for PATCH/GET tests."""
    record = CoachVideoClassification(
        coach_name="孙浩泓",
        course_series="小孙专业乒乓球—全套正反手体系课程_33节",
        cos_object_key=f"charhuang/tt_video/test_{uuid.uuid4()}.mp4",
        filename="22_正手拉球练习.mp4",
        tech_category="forehand_topspin",
        tech_tags=[],
        raw_tech_desc="正手拉球",
        classification_source="rule",
        confidence=1.0,
        kb_extracted=False,
    )
    db_session.add(record)
    await db_session.flush()
    return record


@pytest_asyncio.fixture
async def sample_records(db_session: AsyncSession) -> list[CoachVideoClassification]:
    """Insert multiple records for list/summary tests."""
    records = [
        CoachVideoClassification(
            coach_name="孙浩泓",
            course_series="小孙专业乒乓球—全套正反手体系课程_33节",
            cos_object_key=f"charhuang/tt_video/test_fh_{i}_{uuid.uuid4()}.mp4",
            filename=f"0{i}_正手拉球练习.mp4",
            tech_category="forehand_topspin",
            tech_tags=[],
            classification_source="rule",
            confidence=1.0,
            kb_extracted=(i == 0),  # first record is kb_extracted=True
        )
        for i in range(3)
    ] + [
        CoachVideoClassification(
            coach_name="郭焱",
            course_series="郭焱乒乓球教学-课程全集_郭焱 107节",
            cos_object_key=f"charhuang/tt_video/test_gy_{i}_{uuid.uuid4()}.mp4",
            filename=f"0{i}_发球练习.mp4",
            tech_category="serve",
            tech_tags=[],
            classification_source="llm",
            confidence=0.9,
            kb_extracted=False,
        )
        for i in range(2)
    ]
    for r in records:
        db_session.add(r)
    await db_session.flush()
    return records


# ---------------------------------------------------------------------------
# POST /scan tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.integration
async def test_trigger_scan_full_returns_202(client: AsyncClient):
    """POST /scan with scan_mode=full should return 202 and task_id."""
    with patch("src.workers.classification_task.scan_cos_videos.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock()
        response = await client.post(
            "/api/v1/classifications/scan",
            json={"scan_mode": "full"},
        )
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "pending"
    assert uuid.UUID(data["task_id"])  # valid UUID


@pytest.mark.asyncio
@pytest.mark.integration
async def test_trigger_scan_incremental_returns_202(client: AsyncClient):
    with patch("src.workers.classification_task.scan_cos_videos.apply_async") as mock_apply:
        mock_apply.return_value = MagicMock()
        response = await client.post(
            "/api/v1/classifications/scan",
            json={"scan_mode": "incremental"},
        )
    assert response.status_code == 202
    assert response.json()["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_trigger_scan_invalid_mode_returns_400(client: AsyncClient):
    response = await client.post(
        "/api/v1/classifications/scan",
        json={"scan_mode": "unknown"},
    )
    assert response.status_code == 400
    assert "invalid scan_mode" in response.json()["detail"]


# ---------------------------------------------------------------------------
# GET /scan/{task_id} tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_scan_status_pending(client: AsyncClient):
    """Non-existent task_id returns pending status."""
    task_id = str(uuid.uuid4())
    response = await client.get(f"/api/v1/classifications/scan/{task_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_scan_status_success(client: AsyncClient):
    """Completed task returns success with stats."""
    task_id = str(uuid.uuid4())
    mock_result = MagicMock()
    mock_result.state = "SUCCESS"
    mock_result.result = {
        "task_id": task_id,
        "scan_mode": "full",
        "scanned": 100,
        "inserted": 80,
        "updated": 15,
        "skipped": 5,
        "errors": 0,
        "elapsed_s": 12.4,
        "error_detail": None,
    }
    with patch("src.api.routers.classifications.AsyncResult", return_value=mock_result):
        response = await client.get(f"/api/v1/classifications/scan/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["scanned"] == 100
    assert data["inserted"] == 80


# ---------------------------------------------------------------------------
# GET /classifications tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_classifications_returns_all(
    client: AsyncClient, sample_records: list
):
    """GET /classifications returns all inserted records."""
    response = await client.get("/api/v1/classifications")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= len(sample_records)
    assert isinstance(data["items"], list)
    # Verify item structure
    if data["items"]:
        item = data["items"][0]
        assert "id" in item
        assert "coach_name" in item
        assert "tech_category" in item
        assert "kb_extracted" in item
        assert "cos_object_key" in item


@pytest.mark.asyncio
@pytest.mark.integration
async def test_filter_by_coach_name(client: AsyncClient, sample_records: list):
    response = await client.get("/api/v1/classifications?coach_name=郭焱")
    assert response.status_code == 200
    data = response.json()
    assert all(item["coach_name"] == "郭焱" for item in data["items"])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_filter_kb_extracted_false(client: AsyncClient, sample_records: list):
    """kb_extracted=false returns only unextracted records."""
    response = await client.get("/api/v1/classifications?kb_extracted=false")
    assert response.status_code == 200
    items = response.json()["items"]
    assert all(item["kb_extracted"] is False for item in items)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_filter_kb_extracted_true(client: AsyncClient, sample_records: list):
    response = await client.get("/api/v1/classifications?kb_extracted=true")
    assert response.status_code == 200
    items = response.json()["items"]
    assert all(item["kb_extracted"] is True for item in items)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_filter_tech_category(client: AsyncClient, sample_records: list):
    response = await client.get("/api/v1/classifications?tech_category=serve")
    assert response.status_code == 200
    items = response.json()["items"]
    assert all(item["tech_category"] == "serve" for item in items)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pagination(client: AsyncClient, sample_records: list):
    response = await client.get("/api/v1/classifications?limit=2&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) <= 2
    assert data["total"] >= len(sample_records)


# ---------------------------------------------------------------------------
# GET /classifications/summary tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.integration
async def test_summary_returns_coaches(client: AsyncClient, sample_records: list):
    response = await client.get("/api/v1/classifications/summary")
    assert response.status_code == 200
    data = response.json()
    assert "coaches" in data
    # Check structure
    if data["coaches"]:
        coach = data["coaches"][0]
        assert "coach_name" in coach
        assert "total_videos" in coach
        assert "kb_extracted" in coach
        assert "tech_breakdown" in coach


@pytest.mark.asyncio
@pytest.mark.integration
async def test_summary_filter_by_coach(client: AsyncClient, sample_records: list):
    response = await client.get("/api/v1/classifications/summary?coach_name=孙浩泓")
    assert response.status_code == 200
    data = response.json()
    assert all(c["coach_name"] == "孙浩泓" for c in data["coaches"])


# ---------------------------------------------------------------------------
# PATCH /classifications/{id} tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.integration
async def test_patch_classification_success(
    client: AsyncClient, sample_record: CoachVideoClassification
):
    """PATCH should update tech_category and set source=manual."""
    response = await client.patch(
        f"/api/v1/classifications/{sample_record.id}",
        json={"tech_category": "backhand_topspin", "tech_tags": ["footwork"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["tech_category"] == "backhand_topspin"
    assert data["classification_source"] == "manual"
    assert data["confidence"] == 1.0
    assert "footwork" in data["tech_tags"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_patch_invalid_tech_category_returns_400(
    client: AsyncClient, sample_record: CoachVideoClassification
):
    response = await client.patch(
        f"/api/v1/classifications/{sample_record.id}",
        json={"tech_category": "not_a_real_category"},
    )
    assert response.status_code == 400
    assert "invalid tech_category" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_patch_nonexistent_record_returns_404(client: AsyncClient):
    fake_id = str(uuid.uuid4())
    response = await client.patch(
        f"/api/v1/classifications/{fake_id}",
        json={"tech_category": "serve"},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]
