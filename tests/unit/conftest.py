"""conftest.py — shared fixtures for unit tests.

Uses httpx.AsyncClient with FastAPI's ASGI transport.
All external dependencies (DB, COS, Celery) are mocked.
"""
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# ── Provide required env vars before any src imports ──────────────────────────
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("COS_SECRET_ID", "test-id")
os.environ.setdefault("COS_SECRET_KEY", "test-key")
os.environ.setdefault("COS_REGION", "ap-guangzhou")
os.environ.setdefault("COS_BUCKET", "test-bucket")

# ── Import all real ORM models to ensure mapper resolves all forward references ─
# The real models for US2/US3 are now implemented; no stubs needed.
import src.models  # noqa: F401  — triggers __init__.py which imports all models


@pytest.fixture
def app() -> FastAPI:
    """Return a fresh FastAPI app with DB dependency overridden."""
    from src.config import get_settings
    from src.db.session import get_db

    get_settings.cache_clear()

    from src.api.errors import register_exception_handlers
    from src.api.routers import knowledge_base, tasks

    _app = FastAPI()
    # Feature-017：unit 测试 app 也必须注册异常处理器，否则 AppException 不会被
    # 转换为 ErrorEnvelope JSON 响应，会作为未捕获异常直接冒泡导致测试失败。
    register_exception_handlers(_app)
    _app.include_router(tasks.router, prefix="/api/v1")
    _app.include_router(knowledge_base.router, prefix="/api/v1")
    return _app


@pytest.fixture
def mock_db():
    """AsyncMock that behaves like an AsyncSession."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def override_db(app, mock_db):
    """Override get_db dependency with mock_db."""
    from src.db.session import get_db

    async def _get_mock_db():
        yield mock_db

    app.dependency_overrides[get_db] = _get_mock_db
    yield mock_db
    app.dependency_overrides.clear()


@pytest.fixture
async def client(app) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Shared test data ──────────────────────────────────────────────────────────

TASK_ID = uuid.uuid4()
COS_KEY = "coach-videos/test.mp4"
KB_VERSION = "1.1.0"


def make_task(
    status="pending",
    task_type="expert_video",
    deleted_at=None,
    kb_version=None,
):
    from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType

    t = MagicMock(spec=AnalysisTask)
    t.id = TASK_ID
    t.task_type = TaskType(task_type)
    t.status = TaskStatus(status)
    t.created_at = datetime(2026, 4, 18, 10, 0, 0)
    t.started_at = None
    t.completed_at = None
    t.video_duration_seconds = None
    t.video_fps = None
    t.video_resolution = None
    t.deleted_at = deleted_at
    t.knowledge_base_version = kb_version
    # Feature 002: long video progress fields
    t.progress_pct = None
    t.processed_segments = None
    t.total_segments = None
    t.audio_fallback_reason = None
    # Feature 006: coach fields
    t.coach_id = None
    t.coach = None
    # Feature 007: timing stats
    t.timing_stats = None
    return t


def make_kb(version=KB_VERSION, status="draft"):
    from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase

    kb = MagicMock(spec=TechKnowledgeBase)
    kb.version = version
    kb.status = KBStatus(status)
    kb.action_types_covered = ["forehand_topspin"]
    kb.point_count = 2
    kb.approved_by = None
    kb.approved_at = None
    kb.created_at = datetime(2026, 4, 18, 9, 0, 0)
    kb.notes = None
    return kb


def make_tech_point(version=KB_VERSION):
    from src.models.expert_tech_point import ActionType, ExpertTechPoint

    p = MagicMock(spec=ExpertTechPoint)
    p.action_type = ActionType.forehand_topspin
    p.dimension = "elbow_angle"
    p.param_min = 90.0
    p.param_max = 130.0
    p.param_ideal = 110.0
    p.unit = "°"
    p.extraction_confidence = 0.91
    p.knowledge_base_version = version
    p.source_video_id = TASK_ID
    # Feature 002: source annotation fields
    p.source_type = "visual"
    p.conflict_flag = False
    p.conflict_detail = None
    return p
