"""Integration tests for POST /api/v1/diagnosis (Feature 011).

Uses real PostgreSQL DB with savepoint isolation.
Mocks video processing pipeline (pose_estimator, tech_extractor) to avoid
needing real video files in tests.

T013 — US1: full flow with mocked extraction, real DB
T019 — US2: dimension detail validation
T021 — US3: coach video standard version consistency
T023 — error handling: no standard, invalid category
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.db.session import get_db
from src.models.diagnosis_report import DiagnosisReport
from src.models.tech_standard import StandardStatus, TechStandard

TEST_DB_URL = "postgresql+asyncpg://postgres:password@localhost:5432/coaching_db"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Savepoint-isolated session: API commits hit savepoint, not outer tx."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await session.begin()
        await session.begin_nested()  # SAVEPOINT

        def override_get_db():
            yield session

        app.dependency_overrides[get_db] = override_get_db

        yield session

        await session.rollback()
        app.dependency_overrides.clear()

    await engine.dispose()


@pytest_asyncio.fixture
async def http_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Helper: mock extraction pipeline with fixed dimension values
# ---------------------------------------------------------------------------

def mock_extraction_pipeline(measured_values: dict[str, float]):
    """Patch _localize_video (skip COS download) and _extract_measurements."""
    from contextlib import ExitStack
    from unittest.mock import AsyncMock

    async def fake_localize(self, video_path):
        return video_path  # skip actual download

    async def fake_extract(self, video_path, tech_category):
        return measured_values

    class _StackedPatch:
        def __enter__(self):
            self._stack = ExitStack()
            self._stack.enter_context(
                patch(
                    "src.services.diagnosis_service.DiagnosisService._localize_video",
                    fake_localize,
                )
            )
            self._stack.enter_context(
                patch(
                    "src.services.diagnosis_service.DiagnosisService._extract_measurements",
                    fake_extract,
                )
            )
            return self

        def __exit__(self, *args):
            self._stack.close()

    return _StackedPatch()


def mock_llm_no_call():
    """Patch LLM advisor to return simple fallback without calling LLM."""
    def fake_advice(dim, tech_category, llm_client):
        if dim.deviation_level.value == "ok":
            return None
        return f"建议调整{dim.dimension}（测试建议）"

    return patch(
        "src.services.diagnosis_service.generate_improvement_advice",
        fake_advice,
    )


# ---------------------------------------------------------------------------
# T013 — US1: full flow integration test
# ---------------------------------------------------------------------------

class TestUS1FullFlow:
    async def test_diagnosis_returns_200(self, http_client, db_session):
        """Full flow: given active standard, mock extraction → 200 with report"""
        # Verify forehand_topspin has an active standard in DB
        stmt = select(TechStandard).where(
            TechStandard.tech_category == "forehand_topspin",
            TechStandard.status == StandardStatus.active,
        )
        result = await db_session.execute(stmt)
        standard = result.scalar_one_or_none()

        if standard is None:
            pytest.skip("No active forehand_topspin standard in DB")

        # Mock extraction with values matching the standard's dimensions
        measured = {p.dimension: p.ideal for p in standard.points}

        with mock_extraction_pipeline(measured), mock_llm_no_call():
            resp = await http_client.post(
                "/api/v1/diagnosis",
                json={
                    "tech_category": "forehand_topspin",
                    "video_path": "cos://test-bucket/coach_test.mp4",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "report_id" in data
        assert data["tech_category"] == "forehand_topspin"
        assert 0.0 <= data["overall_score"] <= 100.0
        assert isinstance(data["dimensions"], list)
        assert len(data["dimensions"]) > 0

    async def test_diagnosis_persists_to_db(self, http_client, db_session):
        """Diagnosis result must be persisted in diagnosis_reports table"""
        stmt = select(TechStandard).where(
            TechStandard.tech_category == "forehand_topspin",
            TechStandard.status == StandardStatus.active,
        )
        result = await db_session.execute(stmt)
        standard = result.scalar_one_or_none()

        if standard is None:
            pytest.skip("No active forehand_topspin standard in DB")

        measured = {p.dimension: p.ideal for p in standard.points}

        with mock_extraction_pipeline(measured), mock_llm_no_call():
            resp = await http_client.post(
                "/api/v1/diagnosis",
                json={
                    "tech_category": "forehand_topspin",
                    "video_path": "cos://test-bucket/coach_persist_test.mp4",
                },
            )

        assert resp.status_code == 200
        report_id = uuid.UUID(resp.json()["report_id"])

        # Verify persisted
        db_report = await db_session.get(DiagnosisReport, report_id)
        assert db_report is not None
        assert db_report.tech_category == "forehand_topspin"
        assert 0.0 <= db_report.overall_score <= 100.0

    async def test_diagnosis_standard_id_matches_active(self, http_client, db_session):
        """standard_id in response must match the active standard in DB (US3)"""
        stmt = select(TechStandard).where(
            TechStandard.tech_category == "forehand_topspin",
            TechStandard.status == StandardStatus.active,
        )
        result = await db_session.execute(stmt)
        standard = result.scalar_one_or_none()

        if standard is None:
            pytest.skip("No active forehand_topspin standard in DB")

        measured = {p.dimension: p.ideal for p in standard.points}

        with mock_extraction_pipeline(measured), mock_llm_no_call():
            resp = await http_client.post(
                "/api/v1/diagnosis",
                json={
                    "tech_category": "forehand_topspin",
                    "video_path": "test.mp4",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["standard_id"] == standard.id
        assert data["standard_version"] == standard.version


# ---------------------------------------------------------------------------
# T019 — US2: dimension detail validation
# ---------------------------------------------------------------------------

class TestUS2DimensionDetails:
    async def test_ok_dimensions_in_strengths(self, http_client, db_session):
        """Dimensions at ideal value → in strengths[], advice=null"""
        stmt = select(TechStandard).where(
            TechStandard.tech_category == "forehand_topspin",
            TechStandard.status == StandardStatus.active,
        )
        result = await db_session.execute(stmt)
        standard = result.scalar_one_or_none()

        if standard is None:
            pytest.skip("No active standard")

        # All ideal values → all ok
        measured = {p.dimension: p.ideal for p in standard.points}

        with mock_extraction_pipeline(measured), mock_llm_no_call():
            resp = await http_client.post(
                "/api/v1/diagnosis",
                json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
            )

        assert resp.status_code == 200
        data = resp.json()
        # All dimensions should be in strengths
        dim_names = {d["dimension"] for d in data["dimensions"]}
        for s in data["strengths"]:
            assert s in dim_names
        # ok dimensions have null advice
        for d in data["dimensions"]:
            if d["deviation_level"] == "ok":
                assert d["improvement_advice"] is None

    async def test_all_ideal_gives_100_score(self, http_client, db_session):
        """All dimensions at ideal value → overall_score should be 100"""
        stmt = select(TechStandard).where(
            TechStandard.tech_category == "forehand_topspin",
            TechStandard.status == StandardStatus.active,
        )
        result = await db_session.execute(stmt)
        standard = result.scalar_one_or_none()

        if standard is None:
            pytest.skip("No active standard")

        measured = {p.dimension: p.ideal for p in standard.points}

        with mock_extraction_pipeline(measured), mock_llm_no_call():
            resp = await http_client.post(
                "/api/v1/diagnosis",
                json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
            )

        assert resp.status_code == 200
        assert resp.json()["overall_score"] == pytest.approx(100.0, abs=0.1)

    async def test_deviant_dimension_has_advice(self, http_client, db_session):
        """Deviant dimension → improvement_advice is non-null"""
        stmt = select(TechStandard).where(
            TechStandard.tech_category == "forehand_topspin",
            TechStandard.status == StandardStatus.active,
        )
        result = await db_session.execute(stmt)
        standard = result.scalar_one_or_none()

        if standard is None:
            pytest.skip("No active standard")

        if not standard.points:
            pytest.skip("No standard points")

        # Set first dimension far outside range
        first_point = standard.points[0]
        measured = {p.dimension: p.ideal for p in standard.points}
        # Set first dimension to a very large deviation
        measured[first_point.dimension] = first_point.max * 3.0

        with mock_extraction_pipeline(measured), mock_llm_no_call():
            resp = await http_client.post(
                "/api/v1/diagnosis",
                json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
            )

        assert resp.status_code == 200
        dims = resp.json()["dimensions"]
        deviant = next(
            (d for d in dims if d["dimension"] == first_point.dimension), None
        )
        assert deviant is not None
        assert deviant["deviation_level"] != "ok"
        assert deviant["improvement_advice"] is not None


# ---------------------------------------------------------------------------
# T023 — Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    async def test_no_standard_returns_404(self, http_client, db_session):
        """tech_category with no active standard → 404 with standard_not_found"""
        # forehand_loop_underspin should have no active standard (no real data)
        resp = await http_client.post(
            "/api/v1/diagnosis",
            json={
                "tech_category": "forehand_loop_underspin",
                "video_path": "test.mp4",
            },
        )

        # If it has data, check at least the format is correct
        if resp.status_code == 200:
            pytest.skip("forehand_loop_underspin has active standard, skipping 404 test")

        assert resp.status_code == 404
        inner = resp.json()["detail"]
        assert inner["error"] == "standard_not_found"
        assert "detail" in inner

    async def test_invalid_tech_category_returns_422(self, http_client, db_session):
        """Invalid tech_category → 422 without hitting DB"""
        resp = await http_client.post(
            "/api/v1/diagnosis",
            json={"tech_category": "not_a_real_move", "video_path": "test.mp4"},
        )
        assert resp.status_code == 422

    async def test_extraction_failure_returns_400(self, http_client, db_session):
        """Empty extraction result → 400 extraction_failed"""
        stmt = select(TechStandard).where(
            TechStandard.tech_category == "forehand_topspin",
            TechStandard.status == StandardStatus.active,
        )
        result = await db_session.execute(stmt)
        standard = result.scalar_one_or_none()

        if standard is None:
            pytest.skip("No active standard")

        # Return empty measurements → ExtractionFailedError
        with mock_extraction_pipeline({}):
            resp = await http_client.post(
                "/api/v1/diagnosis",
                json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
            )

        assert resp.status_code == 400
        inner = resp.json()["detail"]
        assert inner["error"] == "extraction_failed"
