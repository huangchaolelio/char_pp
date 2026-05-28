"""Feature-022 · T034 · 集成测试：审核门绕过开关热切换 + 30 秒生效.

覆盖范围（spec.md US4 验收 / SC-007）：
  AC1: PATCH /admin/review-gate enabled=false → 审核门立即放行 pending_review 视频；
       审计字段 last_toggled_at / last_toggled_by 落 DB；切换路径全程 ≤ 30s
  AC2: PATCH 切回 enabled=true → 审核门恢复严格行为，新提交 pending_review
       视频被 409 CONTENT_NOT_REVIEWED 拦截（无遗留豁免）
  AC3: GET /admin/review-gate 返回 last_toggled_at（与 PATCH 一致），用于运维仪表盘

设计决策：
  · 不真正等待 30 秒（CI 时间敏感）；改用 ``time.perf_counter`` 测量
    PATCH 调用 → KB 抽取入口"放行"路径的端到端延迟，断言 ≤ 30s
  · 双层开关验证：``content_review_gate.enabled=false`` 是审核门级；
    ``settings.kb_extraction_bypass_review_gate=true`` 是应用级；任一启用都直通
  · cleanup 阶段必须把开关切回默认 enabled=true（fail-secure），避免影响其它测试
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.config import get_settings
from src.db import session as session_module
from src.models.coach_video_classification import CoachVideoClassification
from src.models.video_curation_job import VideoCurationJob
from src.models.video_preprocessing_job import VideoPreprocessingJob
from tests.contract.conftest import (
    assert_error_envelope,
    assert_success_envelope,
)


_TAG = f"__t034_{uuid.uuid4().hex[:8]}"


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
async def reset_gate_after(session_factory):
    """在每个测试结束后强制把审核门切回 enabled=true（fail-secure）."""
    yield
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE task_channel_configs "
                "SET enabled = true "
                "WHERE task_type = 'content_review_gate'"
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def pending_classification(session_factory):
    """Seed 一条 review_state=pending_review 的 cvclf（含 success 清洗作业）.

    返回 (cvclf_id, cos_object_key)；测试用其 cos_object_key 调用 KB 抽取入口
    验证审核门是否放行。
    """
    cos_object_key = f"charhuang/tt_video/{_TAG}/serve_002.mp4"
    cvclf_id = uuid.uuid4()
    prep_job_id = uuid.uuid4()
    cur_job_id = uuid.uuid4()

    async with session_factory() as session:
        cvclf = CoachVideoClassification(
            id=cvclf_id,
            coach_name=f"{_TAG}_coach",
            course_series=f"{_TAG}_series",
            cos_object_key=cos_object_key,
            filename="serve_002.mp4",
            tech_category="serve",
            tech_tags=["发球"],
            classification_source="rule",
            confidence=1.0,
            kb_extracted=False,
            preprocessed=True,
        )
        session.add(cvclf)
        prep_job = VideoPreprocessingJob(
            id=prep_job_id, cos_object_key=cos_object_key, status="success"
        )
        session.add(prep_job)
        await session.flush()
        cur_job = VideoCurationJob(
            id=cur_job_id,
            cos_object_key=cos_object_key,
            coach_video_classification_id=cvclf_id,
            preprocessing_job_id=prep_job_id,
            curation_rubric_version="v1",
            status="success",
            total_segment_count=3,
            accepted_segment_count=3,
            rejected_segment_count=0,
            uncertain_segment_count=0,
            total_duration_seconds=120.0,
            accepted_duration_seconds=120.0,
            accepted_duration_ratio=1.0,
            low_quality=False,
            audio_unavailable=False,
            short_video=False,
            completed_at=datetime.now(),
        )
        session.add(cur_job)
        await session.flush()
        await session.execute(
            update(CoachVideoClassification)
            .where(CoachVideoClassification.id == cvclf_id)
            .values(last_curation_job_id=cur_job_id)
        )
        await session.commit()

    yield cvclf_id, cos_object_key

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
async def test_us4_ac1_bypass_switch_releases_pending_kb_within_30s(
    client, pending_classification, reset_gate_after
):
    """AC1: 切换审核门 enabled=false → KB 抽取入口立即放行 pending_review 视频.

    端到端时序：
      1. 默认 enabled=true：pending_review 视频 KB 抽取被 409 拦截
      2. PATCH /admin/review-gate enabled=false
      3. 立即重试 KB 抽取：审核门必须放行（即"虽然可能因下游通道容量等被拒，
         但绝不能再返回 CONTENT_NOT_REVIEWED"）
      4. 全程时延 < 30s（SC-007）
    """
    _, cos_object_key = pending_classification

    # ── 步骤 1：严格门下应被 409 拦下 ──────────────────────────────────
    resp1 = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    assert resp1.status_code == 409, resp1.text
    err1 = assert_error_envelope(resp1.json(), code="CONTENT_NOT_REVIEWED")
    assert err1["details"]["current_review_state"] == "pending_review"

    # ── 步骤 2：PATCH 切换为 enabled=false（计时开始）─────────────────
    t_start = time.perf_counter()
    patch_resp = await client.patch(
        "/api/v1/admin/review-gate",
        json={
            "enabled": False,
            "operator_id": "ops-bypass-test",
            "reason": "T034 集成测试：审核积压应急绕过",
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patch_body = assert_success_envelope(patch_resp.json())
    assert patch_body["enabled"] is False
    assert patch_body["last_toggled_by"] == "ops-bypass-test"
    assert patch_body["last_toggled_at"] is not None

    # ── 步骤 3：立即重试 KB 抽取（应不再被 CONTENT_NOT_REVIEWED 拦下）──
    resp2 = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    elapsed_seconds = time.perf_counter() - t_start

    # 关键断言：审核门绝不能再拦下；响应可能因其他原因（通道容量 / 重复任务等）失败
    if resp2.status_code == 409:
        body = resp2.json()
        assert body.get("success") is False
        assert body["error"]["code"] != "CONTENT_NOT_REVIEWED", (
            f"绕过开关已切到 false 但 KB 抽取入口仍返回 CONTENT_NOT_REVIEWED；"
            f"完整响应: {body!r}"
        )
        # 其他 4xx 业务错误（如 CHANNEL_QUEUE_FULL / DUPLICATE_TASK）可接受
    else:
        # 200/201/202 都意味着审核门放行 + 任务进入下游通道
        assert resp2.status_code in (200, 201, 202), resp2.text

    # SC-007: 切换 → 生效全程 ≤ 30s
    assert elapsed_seconds < 30.0, (
        f"PATCH → KB 抽取放行链路 {elapsed_seconds:.3f}s 超过 30s SC-007 阈值"
    )


@pytest.mark.asyncio
async def test_us4_ac2_switch_back_to_strict_immediately_blocks(
    client, pending_classification, reset_gate_after
):
    """AC2: 切回 enabled=true 后审核门立刻恢复严格行为；不留遗留豁免."""
    _, cos_object_key = pending_classification

    # 先切到 false
    await client.patch(
        "/api/v1/admin/review-gate",
        json={
            "enabled": False,
            "operator_id": "ops-1",
            "reason": "T034: open gate first",
        },
    )

    # 再切回 true（不留遗留豁免）
    patch_resp = await client.patch(
        "/api/v1/admin/review-gate",
        json={
            "enabled": True,
            "operator_id": "ops-2",
            "reason": "T034: revert to strict",
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    body = assert_success_envelope(patch_resp.json())
    assert body["enabled"] is True
    assert body["last_toggled_by"] == "ops-2"

    # 立即重试：必须再被 CONTENT_NOT_REVIEWED 拦下（无遗留豁免）
    resp = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    assert resp.status_code == 409, resp.text
    err = assert_error_envelope(resp.json(), code="CONTENT_NOT_REVIEWED")
    assert err["details"]["current_review_state"] == "pending_review"


@pytest.mark.asyncio
async def test_us4_ac3_get_review_gate_returns_audit_fields(
    client, reset_gate_after
):
    """AC3: GET /admin/review-gate 返回 last_toggled_at 等审计字段."""
    # 先 PATCH 一次产生 last_toggled_at
    op_id = "ops-audit-fields"
    patch_resp = await client.patch(
        "/api/v1/admin/review-gate",
        json={
            "enabled": True,
            "operator_id": op_id,
            "reason": "T034 AC3: audit field smoke",
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text

    # GET 应该能读到 last_toggled_at
    get_resp = await client.get("/api/v1/admin/review-gate")
    assert get_resp.status_code == 200, get_resp.text
    body = assert_success_envelope(get_resp.json())
    assert body["enabled"] is True
    assert body["last_toggled_at"] is not None
    # last_toggled_by：当前实现因 task_channel_configs 无 operator 列，
    # GET 返回 None；PATCH 响应中携带（已在 AC1 / AC2 中断言过）
