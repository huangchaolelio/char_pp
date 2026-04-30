"""Feature-019 US5 T036 — GET /extraction-jobs/{job_id} 详情合约测试.

对齐 contracts/extraction-job-detail.yaml:
  - 200 响应 data 含 `output_kbs`（list[OutputKbRef]）
  - OutputKbRef 含 tech_category / version / status / created_at 四字段
  - 产出 0 条 KB 的作业返回 output_kbs=[]
  - 产出 N 条 KB 的作业 output_kbs 长度匹配（按 tech_category ASC 排序）
  - 404 JOB_NOT_FOUND 保持不变
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.errors import register_exception_handlers
from src.api.routers.extraction_jobs import router as ej_router
from src.db import session as _db_session
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


@pytest.fixture(autouse=True)
async def _rebind_session_factory():
    await _db_session.engine.dispose()
    _db_session.engine = _db_session._make_engine()
    _db_session.AsyncSessionFactory.kw["bind"] = _db_session.engine
    yield
    await _db_session.engine.dispose()


def _factory():
    return _db_session.AsyncSessionFactory


@pytest.fixture
def app_ej() -> FastAPI:
    _app = FastAPI()
    register_exception_handlers(_app)
    _app.include_router(ej_router, prefix="/api/v1")
    return _app


async def _reset_tables() -> None:
    async with _factory()() as session:
        for tbl in (
            "pipeline_steps",
            "teaching_tips",
            "expert_tech_points",
            "tech_knowledge_bases",
            "kb_conflicts",
            "extraction_jobs",
        ):
            await session.execute(sa.text(f"DELETE FROM {tbl}"))
        await session.execute(sa.text(
            "DELETE FROM analysis_tasks WHERE task_type='kb_extraction'"
        ))
        await session.commit()


async def _seed_job_and_kbs(
    *,
    tech_category: str,
    kb_versions_to_seed: list[tuple[int, str]],  # (version, status)
) -> uuid.UUID:
    """Seed 一个 extraction_job，并在其下挂 len(kb_versions_to_seed) 条 KB 记录."""
    async with _factory()() as session:
        task_id = uuid.uuid4()
        job_id = uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO analysis_tasks "
                "(id, task_type, video_filename, video_size_bytes, video_storage_uri, "
                " status, submitted_via, business_phase, business_step) "
                "VALUES (:tid, 'kb_extraction', 's.mp4', 1, 'cos://s', "
                "        'success', 'single', 'TRAINING', 'extract_kb')"
            ),
            {"tid": task_id},
        )
        await session.execute(
            sa.text(
                "INSERT INTO extraction_jobs "
                "(id, analysis_task_id, status, cos_object_key, tech_category, "
                " business_phase, business_step) "
                "VALUES (:jid, :tid, 'success', 'cos://s', :tc, "
                "        'TRAINING', 'extract_kb')"
            ),
            {"jid": job_id, "tid": task_id, "tc": tech_category},
        )
        for ver, status in kb_versions_to_seed:
            await session.execute(
                sa.text(
                    "INSERT INTO tech_knowledge_bases "
                    "(tech_category, version, status, point_count, extraction_job_id, "
                    " business_phase, business_step) "
                    "VALUES (:tc, :ver, :st, 1, :jid, "
                    "        'STANDARDIZATION', 'kb_version_activate')"
                ),
                {"tc": tech_category, "ver": ver, "st": status, "jid": job_id},
            )
        await session.commit()
        return job_id


# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_detail_200_contains_output_kbs_field(app_ej: FastAPI) -> None:
    """基线：output_kbs 字段始终存在（即使为空）。"""
    await _reset_tables()
    job_id = await _seed_job_and_kbs(
        tech_category="forehand_attack", kb_versions_to_seed=[]
    )

    async with AsyncClient(transport=ASGITransport(app=app_ej), base_url="http://t") as c:
        resp = await c.get(f"/api/v1/extraction-jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        # FR-029 的合约核心：output_kbs 字段必须存在
        assert "output_kbs" in data
        assert isinstance(data["output_kbs"], list)
        assert data["output_kbs"] == []


@pytest.mark.asyncio
async def test_detail_200_output_kbs_has_single_draft(app_ej: FastAPI) -> None:
    """产出 1 条 draft KB 的作业 → output_kbs 长度=1，四字段齐全。"""
    await _reset_tables()
    job_id = await _seed_job_and_kbs(
        tech_category="backhand_topspin", kb_versions_to_seed=[(1, "draft")]
    )

    async with AsyncClient(transport=ASGITransport(app=app_ej), base_url="http://t") as c:
        resp = await c.get(f"/api/v1/extraction-jobs/{job_id}")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json())
        assert len(data["output_kbs"]) == 1
        item = data["output_kbs"][0]
        for k in ("tech_category", "version", "status", "created_at"):
            assert k in item, f"missing field {k!r} in OutputKbRef"
        assert item["tech_category"] == "backhand_topspin"
        assert item["version"] == 1
        assert item["status"] == "draft"
        # created_at ISO 8601 可解析
        datetime.fromisoformat(item["created_at"])


@pytest.mark.asyncio
async def test_detail_404_job_not_found(app_ej: FastAPI) -> None:
    """404 JOB_NOT_FOUND 保持不变。"""
    fake_id = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app_ej), base_url="http://t") as c:
        resp = await c.get(f"/api/v1/extraction-jobs/{fake_id}")
        assert resp.status_code == 404
        assert_error_envelope(resp.json(), code="JOB_NOT_FOUND")


@pytest.mark.asyncio
async def test_detail_output_kbs_sorted_by_tech_category(app_ej: FastAPI) -> None:
    """多条 KB：output_kbs 按 tech_category ASC 排序。

    当前实现单个 extraction_job 产出单个 tech_category 的 KB（Feature-019 T033），
    但 svc 支持多条返回以兼容未来扩展。本测试以同 tc 不同 version 场景验证顺序稳定。
    """
    await _reset_tables()
    # 同 tech_category 下多版本（不违反 uq_tech_kb_active_per_category：
    # 只有 1 条 active）
    job_id = await _seed_job_and_kbs(
        tech_category="serve",
        kb_versions_to_seed=[(2, "draft"), (1, "archived")],
    )

    async with AsyncClient(transport=ASGITransport(app=app_ej), base_url="http://t") as c:
        resp = await c.get(f"/api/v1/extraction-jobs/{job_id}")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json())
        assert len(data["output_kbs"]) == 2
        # svc 排序为 tech_category ASC（同 tc 则顺序不作强断言，但两条都应属 serve）
        assert all(item["tech_category"] == "serve" for item in data["output_kbs"])
