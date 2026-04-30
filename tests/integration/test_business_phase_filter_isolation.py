"""Feature-020 · T047 · US4 监控隔离集成测试.

验证 `GET /api/v1/tasks?business_phase=INFERENCE|TRAINING` 的过滤能正确
把运动员侧任务与教练侧任务隔离开（FR-007 + SC-006）。

Seed 结构（两侧各 3 条，合计 6 条）：
  · TRAINING 侧（教练）：
      scan_cos_videos     (task_type=video_classification)
      preprocess_video    (task_type=video_preprocessing)
      extract_kb          (task_type=kb_extraction)
  · INFERENCE 侧（运动员）：
      scan_athlete_videos     (task_type=athlete_video_classification)
      preprocess_athlete_video (task_type=athlete_video_preprocessing)
      diagnose_athlete        (task_type=athlete_diagnosis)

断言：
  1. `?business_phase=INFERENCE` 返回恰好 3 条，且都是运动员侧 task_type
  2. `?business_phase=TRAINING` 返回恰好 3 条，且都是教练侧 task_type
  3. 两次过滤求和 == 全量 seed 数量（6）
  4. `?business_phase=INFERENCE&business_step=preprocess_athlete_video` 精确 1 条
  5. `?business_phase=TRAINING&business_step=scan_athlete_videos`
     → 400 INVALID_PHASE_STEP_COMBO（运动员步骤不属于训练阶段）
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.config import get_settings
from src.db import session as session_module
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


_TAG = f"__t047_{uuid.uuid4().hex[:8]}"  # 唯一隔离标记


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=False,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    orig_engine = session_module.engine
    orig_factory = session_module.AsyncSessionFactory
    session_module.engine = engine
    session_module.AsyncSessionFactory = factory
    try:
        yield factory
    finally:
        session_module.engine = orig_engine
        session_module.AsyncSessionFactory = orig_factory
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def six_seeded_tasks(session_factory):
    """Seed 6 条任务（TRAINING 3 + INFERENCE 3），用唯一 _TAG 隔离查询."""
    specs = [
        # (task_type, expected_phase, expected_step)
        (TaskType.video_classification, "TRAINING", "scan_cos_videos"),
        (TaskType.video_preprocessing, "TRAINING", "preprocess_video"),
        (TaskType.kb_extraction, "TRAINING", "extract_kb"),
        (TaskType.athlete_video_classification, "INFERENCE", "scan_athlete_videos"),
        (TaskType.athlete_video_preprocessing, "INFERENCE", "preprocess_athlete_video"),
        (TaskType.athlete_diagnosis, "INFERENCE", "diagnose_athlete"),
    ]

    created_ids: list[uuid.UUID] = []
    async with session_factory() as session:
        for task_type, _, _ in specs:
            row = AnalysisTask(
                id=uuid.uuid4(),
                task_type=task_type,
                video_filename=f"{_TAG}.mp4",
                video_size_bytes=1024,
                video_storage_uri=f"charhuang/tt_video/{_TAG}/{task_type.value}.mp4",
                status=TaskStatus.pending,
                submitted_via="single",
            )
            session.add(row)
            created_ids.append(row.id)
        await session.commit()

    yield created_ids

    # Cleanup
    async with session_factory() as session:
        await session.execute(
            delete(AnalysisTask).where(AnalysisTask.id.in_(created_ids))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_inference_phase_returns_only_athlete_tasks(
    session_factory, client, six_seeded_tasks
):
    """business_phase=INFERENCE 只能看到 3 条运动员任务."""
    resp = await client.get(
        f"/api/v1/tasks?page_size=100&business_phase=INFERENCE"
        f"&video_filename_like={_TAG}"  # 不存在的参数会被忽略；此处只为留痕
    )
    # 回退为无过滤 tag；FastAPI 会忽略未声明参数
    assert resp.status_code == 200, resp.text
    data = assert_success_envelope(resp.json())

    # 过滤本次 seed（用 video_storage_uri 前缀）
    ours = [t for t in data if _TAG in t.get("video_filename", "")]
    assert len(ours) == 3, f"Expected 3 INFERENCE tasks, got {len(ours)}"
    athlete_types = {
        "athlete_video_classification",
        "athlete_video_preprocessing",
        "athlete_diagnosis",
    }
    got_types = {t["task_type"] for t in ours}
    assert got_types == athlete_types, f"got task_types={got_types}"


@pytest.mark.asyncio
async def test_training_phase_returns_only_coach_tasks(
    session_factory, client, six_seeded_tasks
):
    """business_phase=TRAINING 只能看到 3 条教练侧任务."""
    resp = await client.get("/api/v1/tasks?page_size=100&business_phase=TRAINING")
    assert resp.status_code == 200, resp.text
    data = assert_success_envelope(resp.json())

    ours = [t for t in data if _TAG in t.get("video_filename", "")]
    assert len(ours) == 3, f"Expected 3 TRAINING tasks, got {len(ours)}"
    coach_types = {"video_classification", "video_preprocessing", "kb_extraction"}
    got_types = {t["task_type"] for t in ours}
    assert got_types == coach_types, f"got task_types={got_types}"


@pytest.mark.asyncio
async def test_inference_plus_training_equals_all_seeded(
    session_factory, client, six_seeded_tasks
):
    """两阶段求和 == 全量 seed 数（核心隔离证明：无交集/漏项）."""
    inf_resp = await client.get(
        "/api/v1/tasks?page_size=100&business_phase=INFERENCE"
    )
    tr_resp = await client.get(
        "/api/v1/tasks?page_size=100&business_phase=TRAINING"
    )
    assert inf_resp.status_code == 200
    assert tr_resp.status_code == 200

    inf_ours = [
        t for t in assert_success_envelope(inf_resp.json())
        if _TAG in t.get("video_filename", "")
    ]
    tr_ours = [
        t for t in assert_success_envelope(tr_resp.json())
        if _TAG in t.get("video_filename", "")
    ]
    assert len(inf_ours) + len(tr_ours) == 6
    # 交集必须为空
    inf_ids = {t["task_id"] for t in inf_ours}
    tr_ids = {t["task_id"] for t in tr_ours}
    assert inf_ids.isdisjoint(tr_ids), "phase 隔离失败：出现交集"


@pytest.mark.asyncio
async def test_inference_with_specific_step_narrows_to_one(
    session_factory, client, six_seeded_tasks
):
    """business_phase=INFERENCE & business_step=preprocess_athlete_video → 1 条."""
    resp = await client.get(
        "/api/v1/tasks?page_size=100"
        "&business_phase=INFERENCE&business_step=preprocess_athlete_video"
    )
    assert resp.status_code == 200, resp.text
    data = assert_success_envelope(resp.json())
    ours = [t for t in data if _TAG in t.get("video_filename", "")]
    assert len(ours) == 1, f"Expected exactly 1, got {len(ours)}"
    assert ours[0]["task_type"] == "athlete_video_preprocessing"


@pytest.mark.asyncio
async def test_training_phase_with_athlete_step_combo_rejected(
    session_factory, client
):
    """TRAINING + scan_athlete_videos 组合语义矛盾 → 400 INVALID_PHASE_STEP_COMBO."""
    resp = await client.get(
        "/api/v1/tasks?business_phase=TRAINING&business_step=scan_athlete_videos"
        "&task_type=athlete_video_classification"
    )
    # 三元组组合矛盾
    assert resp.status_code == 400, resp.text
    err = assert_error_envelope(resp.json(), code="INVALID_PHASE_STEP_COMBO")
    assert err["details"]["phase"] == "TRAINING"
    assert err["details"]["step"] == "scan_athlete_videos"
