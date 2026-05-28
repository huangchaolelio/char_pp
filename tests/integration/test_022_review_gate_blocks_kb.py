"""Feature-022 · T021 · 集成测试：审核门拦截 KB 抽取（端到端验证）.

覆盖范围（spec.md US2 验收）：
  AC1: review_state=pending_review → POST /tasks/kb-extraction 返回 409 CONTENT_NOT_REVIEWED
  AC2: review_state=rejected       → POST /tasks/kb-extraction 返回 409 CONTENT_REVIEW_REJECTED
  AC3: review_state=stale          → POST /tasks/kb-extraction 返回 409 CONTENT_REVIEW_STALE
  AC4: review_state=approved + 提交 EP-3 决策 approved → POST /tasks/kb-extraction 通过审核门，
       走到下一道闸（其它原因被拒可接受 — 关键是不被审核门拦下）
  AC5: 双层 bypass 任一启用 → 审核门直通；响应中 X-Review-Gate-Bypass 留痕（可选断言）

与 contract 测试的差异：
  · contract 测试侧重单个 endpoint 的合约形态 + 错误码触发
  · integration 测试侧重 **EP-3 决策 → KB 抽取审核门** 的端到端联动：
    用真实 EP-3 接口提交决策（不直接 update 数据库），验证状态机生效后
    KB 抽取入口能立即放行；这是 spec.md FR-008/FR-009 的端到端语义保证。

性能目标（plan.md）：审核门追加延迟 < 20ms（一次 PK lookup）；本测试不做严格性能断言，
仅在端到端流转能完成的前提下做合约级断言。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.config import get_settings
from src.db import session as session_module
from src.models.coach_video_classification import CoachVideoClassification
from src.models.video_curation_job import VideoCurationJob
from src.models.video_preprocessing_job import VideoPreprocessingJob
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


_TAG = f"__t021_{uuid.uuid4().hex[:8]}"


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_size=2, max_overflow=2, pool_pre_ping=False,
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
async def cleansed_classification(session_factory):
    """Seed 一条 cvclf + 关联的 success 清洗作业，让审核门成为 KB 抽取的唯一拦截因素.

    返回 (cvclf_id, cos_object_key)。
    review_state 默认为 pending_review；测试用例可通过 EP-3 接口或直接 update DB
    把它流转到 approved / rejected / stale。
    """
    cos_object_key = f"charhuang/tt_video/{_TAG}/forehand_loop_001.mp4"
    cvclf_id = uuid.uuid4()
    prep_job_id = uuid.uuid4()
    cur_job_id = uuid.uuid4()

    async with session_factory() as session:
        # 1) cvclf 行
        cvclf = CoachVideoClassification(
            id=cvclf_id,
            coach_name=f"{_TAG}_coach",
            course_series=f"{_TAG}_series",
            cos_object_key=cos_object_key,
            filename="forehand_loop_001.mp4",
            tech_category="forehand_loop_fast",
            tech_tags=["正手", "快攻"],
            classification_source="rule",
            confidence=1.0,
            kb_extracted=False,
            preprocessed=True,
        )
        session.add(cvclf)

        # 2) 预处理 success
        prep_job = VideoPreprocessingJob(
            id=prep_job_id,
            cos_object_key=cos_object_key,
            status="success",
        )
        session.add(prep_job)
        await session.flush()

        # 3) 清洗 success
        cur_job = VideoCurationJob(
            id=cur_job_id,
            cos_object_key=cos_object_key,
            coach_video_classification_id=cvclf_id,
            preprocessing_job_id=prep_job_id,
            curation_rubric_version="v1",
            status="success",
            total_segment_count=5,
            accepted_segment_count=5,
            rejected_segment_count=0,
            uncertain_segment_count=0,
            total_duration_seconds=180.0,
            accepted_duration_seconds=180.0,
            accepted_duration_ratio=1.0,
            low_quality=False,
            audio_unavailable=False,
            short_video=False,
            completed_at=datetime.now(),
        )
        session.add(cur_job)
        await session.flush()

        # 4) 反向回填 last_curation_job_id
        await session.execute(
            update(CoachVideoClassification)
            .where(CoachVideoClassification.id == cvclf_id)
            .values(last_curation_job_id=cur_job_id)
        )
        await session.commit()

    yield cvclf_id, cos_object_key

    # Cleanup（顺序：先 cvclf 触发 cur_job CASCADE → 后 prep_job）
    async with session_factory() as session:
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.id == cvclf_id
            )
        )
        await session.execute(
            delete(VideoPreprocessingJob).where(
                VideoPreprocessingJob.cos_object_key == cos_object_key
            )
        )
        await session.commit()


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_us2_ac1_pending_review_blocks_kb_extraction(
    session_factory, client, cleansed_classification
):
    """AC1: pending_review 状态 → KB 抽取入口 409 CONTENT_NOT_REVIEWED."""
    _, cos_object_key = cleansed_classification

    resp = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    assert resp.status_code == 409, resp.text
    err = assert_error_envelope(resp.json(), code="CONTENT_NOT_REVIEWED")
    assert err["details"]["cos_object_key"] == cos_object_key
    assert err["details"]["current_review_state"] == "pending_review"


@pytest.mark.asyncio
async def test_us2_ac2_rejected_blocks_kb_extraction(
    session_factory, client, cleansed_classification
):
    """AC2: 通过 EP-3 接口提交 rejected 决策 → KB 抽取入口 409 CONTENT_REVIEW_REJECTED.

    端到端验证：EP-3 决策接口让状态机生效，KB 抽取入口立即识别。
    """
    cvclf_id, cos_object_key = cleansed_classification

    # 1) 通过 EP-3 提交 rejected 决策（端到端，不直接 update DB）
    decision_resp = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json={
            "decision": "rejected",
            "reason_code": "quality_low",
            "note": "测试用例：手动标记低质",
            "reviewer_id": "ops-it-rejected-test",
            "expected_review_version": 0,
        },
        headers={"X-Reviewer-Id": "ops-it-rejected-test"},
    )
    assert decision_resp.status_code == 200, decision_resp.text
    assert_success_envelope(decision_resp.json())

    # 2) KB 抽取应立即被审核门拦下
    kb_resp = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    assert kb_resp.status_code == 409, kb_resp.text
    err = assert_error_envelope(kb_resp.json(), code="CONTENT_REVIEW_REJECTED")
    assert err["details"]["cos_object_key"] == cos_object_key
    assert err["details"]["current_review_state"] == "rejected"


@pytest.mark.asyncio
async def test_us2_ac3_stale_blocks_kb_extraction(
    session_factory, client, cleansed_classification
):
    """AC3: stale 状态 → KB 抽取入口 409 CONTENT_REVIEW_STALE.

    构造方式：审核 approved → 直接 update DB 把状态改为 stale（模拟重新清洗触发）；
    端到端 stale 触发由 T022 单独验证。
    """
    cvclf_id, cos_object_key = cleansed_classification

    # 1) 先 approved（合规走通 EP-3）
    approve_resp = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json={
            "decision": "approved",
            "reviewer_id": "ops-it-stale-test",
            "expected_review_version": 0,
        },
        headers={"X-Reviewer-Id": "ops-it-stale-test"},
    )
    assert approve_resp.status_code == 200, approve_resp.text

    # 2) DB 模拟 stale（端到端 stale 触发由 T022 验证）
    async with session_factory() as session:
        await session.execute(
            update(CoachVideoClassification)
            .where(CoachVideoClassification.id == cvclf_id)
            .values(review_state="stale", review_version=2)
        )
        await session.commit()

    # 3) KB 抽取应被 stale 拦下
    kb_resp = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    assert kb_resp.status_code == 409, kb_resp.text
    err = assert_error_envelope(kb_resp.json(), code="CONTENT_REVIEW_STALE")
    assert err["details"]["current_review_state"] == "stale"


@pytest.mark.asyncio
async def test_us2_ac4_approved_passes_review_gate(
    session_factory, client, cleansed_classification
):
    """AC4: 通过 EP-3 提交 approved 决策 → KB 抽取入口审核门放行（不被审核门拦截）.

    审核门放行后，下游可能因其它原因（如通道容量、其它业务校验）拒绝；
    本测试只断言"不是被审核门以 3 个 review_* 错误码拦下"。
    """
    cvclf_id, cos_object_key = cleansed_classification

    # 1) EP-3 提交 approved
    approve_resp = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json={
            "decision": "approved",
            "reviewer_id": "ops-it-approved-test",
            "expected_review_version": 0,
        },
        headers={"X-Reviewer-Id": "ops-it-approved-test"},
    )
    assert approve_resp.status_code == 200, approve_resp.text
    data = assert_success_envelope(approve_resp.json())
    assert data["decision"] == "approved"

    # 2) 验证 DB 状态
    async with session_factory() as session:
        cvclf = (
            await session.execute(
                select(CoachVideoClassification).where(
                    CoachVideoClassification.id == cvclf_id
                )
            )
        ).scalar_one()
        assert cvclf.review_state == "approved"
        assert cvclf.review_version == 1
        assert cvclf.last_decision_id is not None
        assert cvclf.pending_since is None

    # 3) KB 抽取应不再被审核门 3 个 review_* 错误码拦下
    kb_resp = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    # 可能 200（接收 + 入队），也可能因下游其它原因 4xx；
    # 但绝不能是审核门 3 个错误码之一
    body = kb_resp.json()
    if not body.get("success", True):
        err_code = body.get("error", {}).get("code", "")
        review_codes = {
            "CONTENT_NOT_REVIEWED",
            "CONTENT_REVIEW_REJECTED",
            "CONTENT_REVIEW_STALE",
        }
        assert err_code not in review_codes, (
            f"approved 状态被审核门误拦：code={err_code} body={body}"
        )


@pytest.mark.asyncio
async def test_us2_ac5_bypass_via_settings_skips_review_gate(
    session_factory, client, cleansed_classification, monkeypatch
):
    """AC5: 全局 bypass 开关启用 → pending_review 也直通审核门.

    bypass 路径与正常路径的核心差异：bypass 命中后 `evaluate_review_gate`
    返回 ``decision="bypassed"`` 直接放行，不读 cvclf 行。
    """
    _, cos_object_key = cleansed_classification

    # 启用 bypass 开关（必须重置 settings 缓存）
    from src.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("KB_EXTRACTION_BYPASS_REVIEW_GATE", "true")
    get_settings.cache_clear()

    try:
        kb_resp = await client.post(
            "/api/v1/tasks/kb-extraction",
            json={"cos_object_key": cos_object_key, "force": False},
        )
        # bypass 启用 → 不应被审核门 3 个错误码拦下（其它原因可接受）
        body = kb_resp.json()
        if not body.get("success", True):
            err_code = body.get("error", {}).get("code", "")
            review_codes = {
                "CONTENT_NOT_REVIEWED",
                "CONTENT_REVIEW_REJECTED",
                "CONTENT_REVIEW_STALE",
            }
            assert err_code not in review_codes, (
                f"bypass 启用后审核门仍拦截：code={err_code}"
            )
    finally:
        # 恢复缓存（防止污染其它测试）
        get_settings.cache_clear()
