"""Feature-022 · T013 · US1 业务流程升级集成测试.

验证 Feature-022 把 TRAINING 中"原始素材 → 可用语料"链路独立成 CONTENT_PREP
阶段后，4 类任务的 ``business_phase`` / ``business_step`` 派生归属正确，
且既有 STANDARDIZATION / INFERENCE 行为无破坏性变化。

覆盖范围（spec.md US1）：
  - AC1: 4 类内容准备阶段任务（scan/preprocess/classify/curate）归属 CONTENT_PREP
  - AC2: kb_extraction 任务归属 TRAINING（上游已审核语料 → 知识库草稿）
  - AC3: GET /tasks?business_phase=CONTENT_PREP 阶段视图独立计数（不与 TRAINING 混算）
  - AC4: STANDARDIZATION（kb_version_activate）/ INFERENCE（diagnose_athlete）
         既有任务的 phase 归属保持不变（基线断言）

性能 / 兼容性约束：
  - GET /tasks 响应 schema 中 ``business_phase`` / ``business_step`` 字段已暴露（T012）
  - phase_step_hook 默认派生（T011）：调用方不需手动传 phase/step

注意：本测试不验证 content_review API（属 US2/US3 范围）；只验证业务阶段路由本身。
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
from tests.contract.conftest import assert_success_envelope


_TAG = f"__t022us1_{uuid.uuid4().hex[:8]}"  # 唯一隔离标记


# ── Fixtures ─────────────────────────────────────────────────────────────


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
    """Seed 6 条任务覆盖 4 类内容准备 + 训练 + 诊断。

    Feature-022 期望分布：
      · CONTENT_PREP: scan_cos_videos / preprocess_video / classify_video / curate_segments  (4)
      · TRAINING:     extract_kb                                                              (1)
      · INFERENCE:    diagnose_athlete                                                         (1)
    """
    # (task_type, parent_scan_task_id 是否非空, expected_phase, expected_step)
    specs = [
        # video_classification 默认（parent_scan IS NULL）→ CONTENT_PREP/scan_cos_videos
        (TaskType.video_classification, False, "CONTENT_PREP", "scan_cos_videos"),
        # video_classification + parent_scan 非空 → CONTENT_PREP/classify_video
        (TaskType.video_classification, True, "CONTENT_PREP", "classify_video"),
        (TaskType.video_preprocessing, False, "CONTENT_PREP", "preprocess_video"),
        (TaskType.video_curation, False, "CONTENT_PREP", "curate_segments"),
        (TaskType.kb_extraction, False, "TRAINING", "extract_kb"),
        (TaskType.athlete_diagnosis, False, "INFERENCE", "diagnose_athlete"),
    ]

    created: list[tuple[uuid.UUID, str, str]] = []
    parent_scan_id: uuid.UUID | None = None

    async with session_factory() as session:
        for task_type, has_parent, exp_phase, exp_step in specs:
            row = AnalysisTask(
                id=uuid.uuid4(),
                task_type=task_type,
                video_filename=f"{_TAG}.mp4",
                video_size_bytes=1024,
                video_storage_uri=f"charhuang/tt_video/{_TAG}/{task_type.value}__{exp_step}.mp4",
                status=TaskStatus.pending,
                submitted_via="single",
            )
            # 第一个 video_classification（has_parent=False）落库后用作后续的 parent_scan
            if has_parent and parent_scan_id is not None:
                row.parent_scan_task_id = parent_scan_id

            session.add(row)
            await session.flush()  # 触发 phase_step_hook，刷出 ID
            created.append((row.id, exp_phase, exp_step))

            # 第一个 scan 任务的 ID 留作后续 classify 的 parent
            if (
                task_type == TaskType.video_classification
                and not has_parent
                and parent_scan_id is None
            ):
                parent_scan_id = row.id

        await session.commit()

    yield created

    # Cleanup
    async with session_factory() as session:
        await session.execute(
            delete(AnalysisTask).where(AnalysisTask.id.in_([cid for cid, _, _ in created]))
        )
        await session.commit()


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_us1_ac1_content_prep_step_derivation(session_factory, six_seeded_tasks):
    """AC1: 4 个 step 全部归属 CONTENT_PREP（DB 层级断言）。

    通过直接读 ORM 行验证 phase_step_hook 派生结果，避开 API 层副作用。
    """
    async with session_factory() as session:
        ids = [cid for cid, _, _ in six_seeded_tasks]
        rows = (
            await session.execute(
                select(AnalysisTask).where(AnalysisTask.id.in_(ids))
            )
        ).scalars().all()
        actual = {row.id: (row.business_phase, row.business_step) for row in rows}

    # 期望：4 个内容准备阶段步骤
    cp_count = sum(1 for cid, exp_phase, _ in six_seeded_tasks if exp_phase == "CONTENT_PREP")
    assert cp_count == 4, f"fixture 期望 4 个 CONTENT_PREP，实际 spec={cp_count}"

    for cid, exp_phase, exp_step in six_seeded_tasks:
        got_phase, got_step = actual[cid]
        assert got_phase == exp_phase, (
            f"task {cid}: expected phase={exp_phase}, got {got_phase} "
            f"(step={got_step})"
        )
        assert got_step == exp_step, (
            f"task {cid}: expected step={exp_step}, got {got_step} "
            f"(phase={got_phase})"
        )


@pytest.mark.asyncio
async def test_us1_ac2_kb_extraction_belongs_to_training(
    session_factory, six_seeded_tasks
):
    """AC2: kb_extraction 任务归属 TRAINING（不再被内容准备步骤混在一起）。"""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AnalysisTask)
                .where(AnalysisTask.id.in_([cid for cid, _, _ in six_seeded_tasks]))
                .where(AnalysisTask.task_type == TaskType.kb_extraction)
            )
        ).scalars().all()

    assert len(rows) == 1, f"期望 1 条 kb_extraction，实际 {len(rows)}"
    row = rows[0]
    assert row.business_phase == "TRAINING", (
        f"kb_extraction 必须归属 TRAINING，实际 {row.business_phase}"
    )
    assert row.business_step == "extract_kb"


@pytest.mark.asyncio
async def test_us1_ac3_phase_view_independent_count_via_api(
    session_factory, client, six_seeded_tasks
):
    """AC3: GET /tasks 阶段视图独立计数 — CONTENT_PREP / TRAINING / INFERENCE 互不混算。

    本测试同时验证 T012 的 schema 改造：响应中 business_phase / business_step 字段
    已对外暴露，并按 ORM 行的实际值落位。
    """
    cp_resp = await client.get("/api/v1/tasks?page_size=100&business_phase=CONTENT_PREP")
    tr_resp = await client.get("/api/v1/tasks?page_size=100&business_phase=TRAINING")
    inf_resp = await client.get("/api/v1/tasks?page_size=100&business_phase=INFERENCE")

    assert cp_resp.status_code == 200, cp_resp.text
    assert tr_resp.status_code == 200, tr_resp.text
    assert inf_resp.status_code == 200, inf_resp.text

    cp_data = assert_success_envelope(cp_resp.json())
    tr_data = assert_success_envelope(tr_resp.json())
    inf_data = assert_success_envelope(inf_resp.json())

    cp_ours = [t for t in cp_data if _TAG in t.get("video_filename", "")]
    tr_ours = [t for t in tr_data if _TAG in t.get("video_filename", "")]
    inf_ours = [t for t in inf_data if _TAG in t.get("video_filename", "")]

    # 4 + 1 + 1 = 6 条 seed，独立计数
    assert len(cp_ours) == 4, (
        f"CONTENT_PREP 期望 4 条，实际 {len(cp_ours)}; "
        f"task_types={[t['task_type'] for t in cp_ours]}"
    )
    assert len(tr_ours) == 1, f"TRAINING 期望 1 条，实际 {len(tr_ours)}"
    assert len(inf_ours) == 1, f"INFERENCE 期望 1 条，实际 {len(inf_ours)}"

    # 三者无交集
    cp_ids = {t["task_id"] for t in cp_ours}
    tr_ids = {t["task_id"] for t in tr_ours}
    inf_ids = {t["task_id"] for t in inf_ours}
    assert cp_ids.isdisjoint(tr_ids), "CONTENT_PREP 与 TRAINING 出现交集"
    assert cp_ids.isdisjoint(inf_ids), "CONTENT_PREP 与 INFERENCE 出现交集"
    assert tr_ids.isdisjoint(inf_ids), "TRAINING 与 INFERENCE 出现交集"

    # T012 schema 改造验证：每条响应都暴露 business_phase / business_step
    for t in cp_ours:
        assert t.get("business_phase") == "CONTENT_PREP", t
        assert t.get("business_step") in {
            "scan_cos_videos", "preprocess_video", "classify_video", "curate_segments"
        }, t
    for t in tr_ours:
        assert t.get("business_phase") == "TRAINING", t
        assert t.get("business_step") == "extract_kb", t


@pytest.mark.asyncio
async def test_us1_ac4_standardization_inference_baseline_unchanged(
    session_factory, client, six_seeded_tasks
):
    """AC4: 基线断言 — STANDARDIZATION 与 INFERENCE 行为无破坏性变化。

    通过 GET /business-workflow/overview 接口验证 4 阶段都正确响应（不报错），
    且 STANDARDIZATION / INFERENCE 的 step 列表保持原样（Feature-022 仅扩了
    CONTENT_PREP，不动 STD / INF 内部步骤）。
    """
    resp = await client.get("/api/v1/business-workflow/overview?window_hours=24")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True, payload
    data = payload["data"]

    # 四阶段全部存在
    assert set(data.keys()) == {"CONTENT_PREP", "TRAINING", "STANDARDIZATION", "INFERENCE"}, (
        f"业务总览必须返回 4 阶段，实际 keys={list(data.keys())}"
    )

    # STANDARDIZATION 步骤保持原样
    std_steps = set(data["STANDARDIZATION"]["steps"].keys())
    assert std_steps == {
        "review_conflicts", "kb_version_activate", "build_standards"
    }, f"STANDARDIZATION 步骤被破坏：{std_steps}"

    # INFERENCE 步骤保持原样
    inf_steps = set(data["INFERENCE"]["steps"].keys())
    assert inf_steps == {
        "scan_athlete_videos", "preprocess_athlete_video", "diagnose_athlete"
    }, f"INFERENCE 步骤被破坏：{inf_steps}"

    # CONTENT_PREP 含 5 步（含 content_review）
    cp_steps = set(data["CONTENT_PREP"]["steps"].keys())
    assert cp_steps == {
        "scan_cos_videos", "preprocess_video", "classify_video",
        "curate_segments", "content_review"
    }, f"CONTENT_PREP 步骤不全：{cp_steps}"

    # TRAINING 现在仅含 extract_kb
    tr_steps = set(data["TRAINING"]["steps"].keys())
    assert tr_steps == {"extract_kb"}, f"TRAINING 应仅含 extract_kb：{tr_steps}"
