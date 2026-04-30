"""Feature-018 T017 — GET /api/v1/business-workflow/overview 合约测试.

覆盖：
- 200 成功（mock pg_class 低行数 ⇒ 完整档）—— data 结构符合 schema
- 200 降级档（mock pg_class 高行数 ⇒ degraded=true + 省略 p50/p95）
- 422 window_hours 越界
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from src.api.errors import register_exception_handlers
from src.api.routers.business_workflow import router as bw_router
from src.db.session import get_db
from tests.contract.conftest import assert_success_envelope, assert_error_envelope


@pytest.fixture
def app_with_bw() -> FastAPI:
    _app = FastAPI()
    register_exception_handlers(_app)
    _app.include_router(bw_router, prefix="/api/v1")
    return _app


@pytest.fixture
def mock_service_full(monkeypatch):
    """Mock WorkflowOverviewService.get_overview 返回完整档快照."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.api.schemas.business_workflow import (
        PhaseSnapshot,
        StepSnapshot,
        WorkflowOverviewMeta,
        WorkflowOverviewSnapshot,
    )
    from src.api.routers import business_workflow as bw_module

    def _training_step(name):
        return StepSnapshot(
            step=name, pending=0, processing=1, success=5, failed=0,
            recent_24h_completed=5, p50_seconds=10.0, p95_seconds=30.0,
        )

    async def fake_overview(self, session, window_hours: int = 24):
        snap = WorkflowOverviewSnapshot(
            TRAINING=PhaseSnapshot(
                phase="TRAINING",
                steps={
                    "scan_cos_videos": _training_step("scan_cos_videos"),
                    "preprocess_video": _training_step("preprocess_video"),
                    "classify_video": _training_step("classify_video"),
                    "extract_kb": _training_step("extract_kb"),
                },
            ),
            STANDARDIZATION=PhaseSnapshot(
                phase="STANDARDIZATION",
                steps={
                    "review_conflicts": StepSnapshot(
                        step="review_conflicts", pending=0, processing=0, success=0,
                        failed=0, recent_24h_completed=0, p50_seconds=None, p95_seconds=None,
                    ),
                    "kb_version_activate": StepSnapshot(
                        step="kb_version_activate", pending=0, processing=0, success=1,
                        failed=0, recent_24h_completed=1, p50_seconds=0.3, p95_seconds=0.5,
                    ),
                    "build_standards": StepSnapshot(
                        step="build_standards", pending=0, processing=0, success=0,
                        failed=0, recent_24h_completed=0, p50_seconds=None, p95_seconds=None,
                    ),
                },
            ),
            INFERENCE=PhaseSnapshot(
                phase="INFERENCE",
                steps={
                    "diagnose_athlete": StepSnapshot(
                        step="diagnose_athlete", pending=1, processing=2, success=8,
                        failed=0, recent_24h_completed=8, p50_seconds=45.2, p95_seconds=92.1,
                    ),
                },
            ),
        )
        meta = WorkflowOverviewMeta(
            generated_at=datetime.now(tz=ZoneInfo("Asia/Shanghai")),
            window_hours=window_hours,
            degraded=False,
            degraded_reason=None,
        )
        return snap, meta

    monkeypatch.setattr(
        bw_module.WorkflowOverviewService, "get_overview", fake_overview
    )


@pytest.fixture
def mock_service_degraded(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.api.schemas.business_workflow import (
        PhaseSnapshot,
        StepSnapshot,
        WorkflowOverviewMeta,
        WorkflowOverviewSnapshot,
    )
    from src.api.routers import business_workflow as bw_module

    def _degraded_step(name):
        return StepSnapshot(
            step=name, pending=0, processing=5, success=2480, failed=18,
            recent_24h_completed=2480, p50_seconds=None, p95_seconds=None,
        )

    async def fake_overview(self, session, window_hours: int = 24):
        snap = WorkflowOverviewSnapshot(
            TRAINING=PhaseSnapshot(phase="TRAINING", steps={"extract_kb": _degraded_step("extract_kb")}),
            STANDARDIZATION=PhaseSnapshot(phase="STANDARDIZATION", steps={}),
            INFERENCE=PhaseSnapshot(phase="INFERENCE", steps={}),
        )
        meta = WorkflowOverviewMeta(
            generated_at=datetime.now(tz=ZoneInfo("Asia/Shanghai")),
            window_hours=window_hours,
            degraded=True,
            degraded_reason="row_count_exceeds_latency_budget",
        )
        return snap, meta

    monkeypatch.setattr(
        bw_module.WorkflowOverviewService, "get_overview", fake_overview
    )


@pytest.fixture
def override_db_simple(app_with_bw):
    from unittest.mock import AsyncMock
    async def _fake_db():
        yield AsyncMock()
    app_with_bw.dependency_overrides[get_db] = _fake_db
    yield
    app_with_bw.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_200_full_envelope(app_with_bw, mock_service_full, override_db_simple):
    async with AsyncClient(transport=ASGITransport(app=app_with_bw), base_url="http://t") as c:
        resp = await c.get("/api/v1/business-workflow/overview")
    assert resp.status_code == 200
    body = resp.json()
    # 顶层信封
    assert body["success"] is True
    assert "data" in body
    assert "meta" in body
    # data 三阶段全齐
    data = body["data"]
    assert set(data.keys()) == {"TRAINING", "STANDARDIZATION", "INFERENCE"}
    # 完整档必须含 p50/p95
    training_extract = data["TRAINING"]["steps"]["extract_kb"]
    assert "p50_seconds" in training_extract
    # meta
    assert body["meta"]["degraded"] is False
    assert "generated_at" in body["meta"]
    assert body["meta"]["window_hours"] == 24


@pytest.mark.asyncio
async def test_200_degraded_envelope(app_with_bw, mock_service_degraded, override_db_simple):
    async with AsyncClient(transport=ASGITransport(app=app_with_bw), base_url="http://t") as c:
        resp = await c.get("/api/v1/business-workflow/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["degraded"] is True
    assert body["meta"]["degraded_reason"] == "row_count_exceeds_latency_budget"
    # 降级档 step 不含 p50/p95
    step = body["data"]["TRAINING"]["steps"]["extract_kb"]
    assert "p50_seconds" not in step or step.get("p50_seconds") is None


@pytest.mark.asyncio
async def test_422_window_hours_out_of_range(app_with_bw, override_db_simple):
    async with AsyncClient(transport=ASGITransport(app=app_with_bw), base_url="http://t") as c:
        resp = await c.get("/api/v1/business-workflow/overview?window_hours=200")
    assert resp.status_code == 422
    body = resp.json()
    assert_error_envelope(body, code="VALIDATION_FAILED")


@pytest.mark.asyncio
async def test_422_window_hours_zero(app_with_bw, override_db_simple):
    async with AsyncClient(transport=ASGITransport(app=app_with_bw), base_url="http://t") as c:
        resp = await c.get("/api/v1/business-workflow/overview?window_hours=0")
    assert resp.status_code == 422
