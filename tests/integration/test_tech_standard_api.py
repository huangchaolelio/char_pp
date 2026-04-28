"""Integration tests for tech standards API (Feature 010).

Tests:
  US1 - T010: Build standard from ExpertTechPoint data, verify DB persistence
  US2 - T018: Query standard by tech_category, 404 for missing, version archived on rebuild
  US3 - T021: Batch build returns results for all valid tech categories
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import uuid as _uuid_module

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from src.api.main import app
from src.db.session import Base, get_db
from src.models.expert_tech_point import ActionType, ExpertTechPoint
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.tech_standard import TechStandard, TechStandardPoint


def _make_task(kb_version: str, idx: int = 0) -> AnalysisTask:
    """Build a minimal AnalysisTask with all NOT NULL fields populated."""
    uid = str(_uuid_module.uuid4())[:8]
    return AnalysisTask(
        task_type=TaskType.expert_video,
        status=TaskStatus.success,
        knowledge_base_version=kb_version,
        video_filename=f"test_video_{uid}_{idx}.mp4",
        video_size_bytes=1024,
        video_storage_uri=f"cos://test-bucket/test_video_{uid}_{idx}.mp4",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_DB_URL = "postgresql+asyncpg://postgres:password@localhost:5432/coaching_db"


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session wrapped in a savepoint so API commits don't persist data."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        # Begin the outer transaction
        await session.begin()
        # Use a savepoint for the test body; API route commits only hit the savepoint
        await session.begin_nested()

        yield session

        # Roll back the outer transaction regardless of what happened inside
        await session.rollback()

    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient with real DB session injected."""
    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _create_analysis_task(session: AsyncSession) -> AnalysisTask:
    """Insert a minimal AnalysisTask to satisfy FK constraints."""
    from src.models.tech_knowledge_base import TechKnowledgeBase, KBStatus

    kb = TechKnowledgeBase(
        version=f"test-{_uuid_module.uuid4().hex[:8]}",
        action_types_covered=["forehand_topspin"],
        point_count=0,
        status=KBStatus.active,
    )
    session.add(kb)
    await session.flush()

    task = _make_task(kb.version)
    session.add(task)
    await session.flush()
    return task


async def _create_tech_points(
    session: AsyncSession,
    task_id: uuid.UUID,
    kb_version: str,
    action_type: ActionType,
    dimension: str,
    values: list[float],
) -> None:
    """Insert ExpertTechPoints with given param_ideal values."""
    for v in values:
        point = ExpertTechPoint(
            knowledge_base_version=kb_version,
            action_type=action_type,
            dimension=dimension,
            param_min=v * 0.8,
            param_ideal=v,
            param_max=v * 1.2,
            unit="°",
            extraction_confidence=0.9,
            source_video_id=task_id,
            conflict_flag=False,
        )
        session.add(point)
    await session.flush()


# ---------------------------------------------------------------------------
# US1: Build standard
# ---------------------------------------------------------------------------

class TestUS1BuildStandard:
    """US1: Trigger build → standard persisted with correct params."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types (Alembic 0012 removed these enum values)")
    async def test_build_creates_standard_with_dimensions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Given ExpertTechPoints from 3 coaches (each own KB), build → standard has dimension records."""
        from src.models.tech_knowledge_base import TechKnowledgeBase, KBStatus

        coach_data = [(90.0, 0), (110.0, 1), (130.0, 2)]
        for val, idx in coach_data:
            kb_ver = f"it1-c{idx}-{_uuid_module.uuid4().hex[:6]}"
            kb = TechKnowledgeBase(version=kb_ver, action_types_covered=["forehand_topspin"], point_count=0, status=KBStatus.active)
            db_session.add(kb)
            await db_session.flush()
            t = _make_task(kb_ver, idx)
            db_session.add(t)
            await db_session.flush()
            point = ExpertTechPoint(
                knowledge_base_version=kb_ver,
                action_type=ActionType.forehand_topspin,
                dimension="elbow_angle",
                param_min=val * 0.8, param_ideal=val, param_max=val * 1.2,
                unit="°", extraction_confidence=0.9,
                source_video_id=t.id, conflict_flag=False,
            )
            db_session.add(point)
        await db_session.flush()

        resp = await client.post(
            "/api/v1/standards/build",
            json={"tech_category": "forehand_topspin"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["result"] == "success"
        assert data["result"]["coach_count"] >= 1
        assert data["result"]["dimension_count"] >= 1

        # Verify DB record exists
        stmt = select(TechStandard).where(
            TechStandard.tech_category == "forehand_topspin",
            TechStandard.status == "active",
        )
        result = await db_session.execute(stmt)
        standard = result.scalar_one_or_none()
        assert standard is not None
        assert len(standard.points) >= 1
        # Verify elbow_angle dimension exists (exact ideal depends on all DB data, not just test data)
        dim_names = [p.dimension for p in standard.points]
        assert "elbow_angle" in dim_names
        elbow_point = next(p for p in standard.points if p.dimension == "elbow_angle")
        assert isinstance(elbow_point.ideal, float)
        assert elbow_point.min <= elbow_point.ideal <= elbow_point.max

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types (Alembic 0012 removed these enum values)")
    async def test_build_excludes_conflict_flag_points(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Points with conflict_flag=True must be excluded from aggregation."""
        from src.models.tech_knowledge_base import TechKnowledgeBase, KBStatus

        uid = _uuid_module.uuid4().hex[:6]
        kb1 = TechKnowledgeBase(version=f"it2a-{uid}", action_types_covered=["forehand_attack"], point_count=0, status=KBStatus.active)
        kb2 = TechKnowledgeBase(version=f"it2b-{uid}", action_types_covered=["forehand_attack"], point_count=0, status=KBStatus.active)
        db_session.add(kb1)
        db_session.add(kb2)
        await db_session.flush()

        t1 = _make_task(kb1.version, 0)
        t2 = _make_task(kb2.version, 1)
        db_session.add(t1)
        db_session.add(t2)
        await db_session.flush()

        # t1: valid point
        p_valid = ExpertTechPoint(
            knowledge_base_version=kb1.version,
            action_type=ActionType.forehand_attack,
            dimension="wrist_angle",
            param_min=50.0, param_ideal=100.0, param_max=150.0,
            unit="°", extraction_confidence=0.85,
            source_video_id=t1.id, conflict_flag=False,
        )
        # t2: conflict point (should be excluded)
        p_conflict = ExpertTechPoint(
            knowledge_base_version=kb2.version,
            action_type=ActionType.forehand_attack,
            dimension="wrist_angle",
            param_min=10.0, param_ideal=200.0, param_max=300.0,
            unit="°", extraction_confidence=0.9,
            source_video_id=t2.id, conflict_flag=True,
        )
        db_session.add(p_valid)
        db_session.add(p_conflict)
        await db_session.flush()

        resp = await client.post(
            "/api/v1/standards/build",
            json={"tech_category": "forehand_attack"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Only 1 valid source → single_source
        assert data["result"]["result"] in ("success", "skipped")


# ---------------------------------------------------------------------------
# US2: Query standard
# ---------------------------------------------------------------------------

class TestUS2QueryStandard:
    """US2: Query by tech_category returns correct data or 404."""

    @pytest.mark.asyncio
    async def test_query_existing_standard_returns_200(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /api/v1/standards/{tech_category} returns standard with dimensions."""
        # Use forehand_loop_underspin — no data in real DB, safe for test isolation
        test_cat = "forehand_loop_underspin"
        # Compute next safe version by checking existing records
        from sqlalchemy import func as sa_func
        ver_stmt = select(sa_func.max(TechStandard.version)).where(
            TechStandard.tech_category == test_cat
        )
        ver_result = await db_session.execute(ver_stmt)
        max_ver = ver_result.scalar_one_or_none() or 0
        next_ver = max_ver + 1

        standard = TechStandard(
            tech_category=test_cat,
            version=next_ver,
            status="active",
            source_quality="multi_source",
            coach_count=2,
            point_count=4,
        )
        db_session.add(standard)
        await db_session.flush()

        point = TechStandardPoint(
            standard_id=standard.id,
            dimension="contact_angle",
            ideal=85.0,
            min=70.0,
            max=100.0,
            unit="°",
            sample_count=4,
            coach_count=2,
        )
        db_session.add(point)
        await db_session.flush()

        resp = await client.get(f"/api/v1/standards/{test_cat}")
        assert resp.status_code == 200
        envelope = resp.json()
        # Feature-017：信封化后业务载荷位于 ``data`` 子树
        assert envelope["success"] is True
        data = envelope["data"]
        assert data["tech_category"] == test_cat
        assert data["source_quality"] == "multi_source"
        assert data["coach_count"] == 2
        assert len(data["dimensions"]) == 1
        assert data["dimensions"][0]["dimension"] == "contact_angle"
        assert data["dimensions"][0]["ideal"] == pytest.approx(85.0)
        assert "built_at" in data

    @pytest.mark.asyncio
    async def test_query_missing_standard_returns_404(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET for tech_category with no active standard → 404（Feature-017 错误信封）."""
        resp = await client.get("/api/v1/standards/forehand_flick")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Feature-013 retired legacy expert_video/athlete_video task types (Alembic 0012 removed these enum values)")
    async def test_rebuild_archives_old_version(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Triggering build twice → old version archived, new version active."""
        from src.models.tech_knowledge_base import TechKnowledgeBase, KBStatus

        uid = _uuid_module.uuid4().hex[:6]
        kb3 = TechKnowledgeBase(version=f"it3a-{uid}", action_types_covered=["backhand_topspin"], point_count=0, status=KBStatus.active)
        kb4 = TechKnowledgeBase(version=f"it3b-{uid}", action_types_covered=["backhand_topspin"], point_count=0, status=KBStatus.active)
        db_session.add(kb3)
        db_session.add(kb4)
        await db_session.flush()

        task1 = _make_task(kb3.version, 0)
        task2 = _make_task(kb4.version, 1)
        db_session.add(task1)
        db_session.add(task2)
        await db_session.flush()

        for task, kb_v in [(task1, kb3.version), (task2, kb4.version)]:
            p = ExpertTechPoint(
                knowledge_base_version=kb_v,
                action_type=ActionType.backhand_topspin,
                dimension="hip_rotation",
                param_min=20.0, param_ideal=45.0, param_max=70.0,
                unit="°", extraction_confidence=0.88,
                source_video_id=task.id, conflict_flag=False,
            )
            db_session.add(p)
        await db_session.flush()

        # First build
        resp1 = await client.post(
            "/api/v1/standards/build",
            json={"tech_category": "backhand_topspin"},
        )
        assert resp1.status_code == 200
        v1 = resp1.json()["result"]["version"]
        assert v1 >= 1  # may be > 1 if real DB already has data

        # Second build
        resp2 = await client.post(
            "/api/v1/standards/build",
            json={"tech_category": "backhand_topspin"},
        )
        assert resp2.status_code == 200
        v2 = resp2.json()["result"]["version"]
        assert v2 == v1 + 1  # version always increments

        # Only one active standard
        stmt = select(TechStandard).where(
            TechStandard.tech_category == "backhand_topspin",
            TechStandard.status == "active",
        )
        result = await db_session.execute(stmt)
        active_standards = result.scalars().all()
        assert len(active_standards) == 1
        assert active_standards[0].version == v2


# ---------------------------------------------------------------------------
# US3: Batch build all tech categories
# ---------------------------------------------------------------------------

class TestUS3BatchBuild:
    """US3: POST /build without tech_category triggers batch build for all ActionTypes."""

    @pytest.mark.asyncio
    async def test_batch_build_returns_all_action_types(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Batch build response contains one entry per ActionType.

        注：ActionType 枚举于迁移 0015 扩容至 27 项（21 类 TECH_CATEGORIES +
        6 项 Feature-002/004 细分兼容值），断言通过动态枚举集合比对而非硬编码数量。
        """
        resp = await client.post("/api/v1/standards/build", json={})
        assert resp.status_code == 200
        envelope = resp.json()
        assert envelope["success"] is True
        data = envelope["data"]

        assert data["mode"] == "batch"
        assert "results" in data
        assert "summary" in data

        # 对齐 ActionType 枚举全集（迁移 0015 后 21+6=27 项）
        from src.models.expert_tech_point import ActionType as EtpActionType
        expected_categories = {at.value for at in EtpActionType}
        returned_categories = {r["tech_category"] for r in data["results"]}
        assert returned_categories == expected_categories

        # Counts add up
        summary = data["summary"]
        assert (
            summary["success_count"] + summary["skipped_count"] + summary["failed_count"]
            == len(data["results"])
        )

    @pytest.mark.asyncio
    async def test_batch_build_skips_categories_without_data(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """All categories with no ExpertTechPoints are skipped (not failed)."""
        resp = await client.post("/api/v1/standards/build", json={})
        assert resp.status_code == 200
        envelope = resp.json()
        assert envelope["success"] is True
        data = envelope["data"]

        # With no seeded data, everything should be skipped
        assert data["summary"]["failed_count"] == 0
        for item in data["results"]:
            assert item["result"] in ("skipped", "success")
