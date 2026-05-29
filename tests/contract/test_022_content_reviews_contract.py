"""Feature-022 · T014 · 内容审核工作台合约测试（TDD Red 阶段）.

严格对齐 ``specs/022-content-review-workflow/contracts/content-reviews.yaml``
与 ``contracts/error-codes.md``。

覆盖范围：
  · 5 个新 endpoint：
      EP-1  GET    /api/v1/content-reviews             列表
      EP-2  GET    /api/v1/content-reviews/{cvclf_id}  详情
      EP-3  POST   /api/v1/content-reviews/{cvclf_id}/decisions  决策
      EP-4  GET    /api/v1/content-reviews/stats       统计
      EP-5a GET    /api/v1/admin/review-gate           开关查询
      EP-5b PATCH  /api/v1/admin/review-gate           开关切换
  · 8 个错误码：
      CONTENT_NOT_REVIEWED        409 — KB 抽取入口校验：pending_review
      CONTENT_REVIEW_REJECTED     409 — KB 抽取入口校验：rejected
      CONTENT_REVIEW_STALE        409 — KB 抽取入口校验：stale
      REVIEW_VERSION_CONFLICT     409 — 决策提交乐观锁
      REVIEW_NOT_PENDING          409 — 决策提交状态机
      INVALID_REVIEWER_IDENTITY   400 — header / body reviewer_id 不一致
      REJECTED_REQUIRES_REASON    400 — decision=rejected 缺 reason_code
      REVIEW_GATE_INVALID_STATE   400 — 开关切换请求体校验

合约稳定性约束：
  - 信封形态：success 与 error 互斥；含 success / data / meta 或 success / error
  - HTTP 状态严格匹配 ERROR_STATUS_MAP
  - error.code 严格匹配 ErrorCode 枚举值

约束执行（章程 II Red 阶段）：
  - 路由 + 服务实现尚未提交时，所有 EP-1~EP-5 用例期望 404 / NotImplementedError；
  - KB 抽取入口 3 个错误码需要服务端模拟数据触发。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

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


_TAG = f"__t014_022_{uuid.uuid4().hex[:8]}"


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
async def seeded_classification(session_factory):
    """Seed 一条 coach_video_classifications 行，默认 review_state=pending_review。

    返回 (id, cos_object_key) 二元组，供后续接口测试使用。
    迁移 0021 + ORM 默认值会让 pending_since=NULL（首次 seed 无需触发）；
    需要 pending_since 时由各用例显式 update。
    """
    cos_object_key = f"charhuang/tt_video/{_TAG}/forehand_001.mp4"
    row = CoachVideoClassification(
        id=uuid.uuid4(),
        coach_name=f"{_TAG}_coach",
        course_series=f"{_TAG}_series",
        cos_object_key=cos_object_key,
        filename="forehand_001.mp4",
        tech_category="forehand_topspin",
        tech_tags=["正手", "拉球"],
        classification_source="rule",
        confidence=1.0,
        kb_extracted=False,
        preprocessed=True,
        # review_state 由 server_default = 'pending_review' 自动落
        # review_version = 0 (默认)
    )
    async with session_factory() as session:
        session.add(row)
        await session.commit()

    yield row.id, cos_object_key

    # Cleanup
    # 顺序：先删 cvclf（CASCADE 触发 video_curation_jobs 一并删除，因 cur_job
    # 通过 coach_video_classification_id 反向 FK 持有 ON DELETE CASCADE）
    # → 再删 video_preprocessing_jobs（cur_job 已不存在，prep_job 的 RESTRICT FK
    # 不再被引用，可安全 DELETE）
    async with session_factory() as session:
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.id == row.id
            )
        )
        await session.execute(
            delete(VideoPreprocessingJob).where(
                VideoPreprocessingJob.cos_object_key == cos_object_key
            )
        )
        await session.commit()


# ══════════════════════════════════════════════════════════════════════════
# EP-1: GET /content-reviews
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ep1_list_default_returns_success_envelope(
    session_factory, client, seeded_classification
):
    """EP-1: 默认列表（不指定 state）返回成功信封 + 含 pagination meta."""
    cvclf_id, _ = seeded_classification
    resp = await client.get("/api/v1/content-reviews?page=1&page_size=20")
    assert resp.status_code == 200, resp.text
    data = assert_success_envelope(resp.json(), expect_meta=True)
    # data 是列表
    assert isinstance(data, list)
    # 验证字段（如能命中本次 seed）
    ours = [t for t in data if str(t.get("id")) == str(cvclf_id)]
    if ours:  # seed 可能因 page_size 不在第一页
        item = ours[0]
        for field in (
            "id", "coach_name", "tech_category", "cos_object_key",
            "filename", "review_state", "review_version",
        ):
            assert field in item, f"missing field {field!r} in {item}"
        assert item["review_state"] == "pending_review"
        assert item["review_version"] == 0


@pytest.mark.asyncio
async def test_ep1_filter_by_state_pending_review(client):
    """EP-1: ?state=pending_review 仅返回该状态条目."""
    resp = await client.get("/api/v1/content-reviews?state=pending_review&page_size=20")
    assert resp.status_code == 200
    data = assert_success_envelope(resp.json(), expect_meta=True)
    for item in data:
        assert item["review_state"] == "pending_review"


@pytest.mark.asyncio
async def test_ep1_invalid_state_returns_400(client):
    """EP-1: 非法 state 值返回 400 INVALID_ENUM_VALUE 或 422 VALIDATION_FAILED."""
    resp = await client.get("/api/v1/content-reviews?state=garbage")
    assert resp.status_code in (400, 422)
    body = resp.json()
    err = assert_error_envelope(body)
    assert err["code"] in {"INVALID_ENUM_VALUE", "VALIDATION_FAILED"}


@pytest.mark.asyncio
async def test_ep1_invalid_page_size_returns_400(client):
    """EP-1: page_size > 100 返回 400 INVALID_PAGE_SIZE 或 422."""
    resp = await client.get("/api/v1/content-reviews?page_size=999")
    assert resp.status_code in (400, 422)
    body = resp.json()
    err = assert_error_envelope(body)
    assert err["code"] in {"INVALID_PAGE_SIZE", "VALIDATION_FAILED"}


# ══════════════════════════════════════════════════════════════════════════
# EP-2: GET /content-reviews/{cvclf_id}
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ep2_detail_returns_success_envelope(
    session_factory, client, seeded_classification
):
    """EP-2: 详情接口返回 SuccessEnvelope，含基础字段（curation_summary 可空）."""
    cvclf_id, _ = seeded_classification
    resp = await client.get(f"/api/v1/content-reviews/{cvclf_id}")
    assert resp.status_code == 200, resp.text
    data = assert_success_envelope(resp.json())
    assert str(data["id"]) == str(cvclf_id)
    assert data["review_state"] == "pending_review"
    # decision_history 是数组（首次为空）
    assert "decision_history" in data
    assert isinstance(data["decision_history"], list)
    # 首次审核前 last_decision = None
    assert data.get("last_decision") is None


@pytest.mark.asyncio
async def test_ep2_not_found_returns_404(client):
    """EP-2: 不存在的 cvclf_id 返回 404 NOT_FOUND."""
    fake_id = uuid.uuid4()
    resp = await client.get(f"/api/v1/content-reviews/{fake_id}")
    assert resp.status_code == 404
    body = resp.json()
    assert_error_envelope(body, code="NOT_FOUND")


# ══════════════════════════════════════════════════════════════════════════
# EP-3: POST /content-reviews/{cvclf_id}/decisions
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ep3_approved_decision_succeeds(
    session_factory, client, seeded_classification
):
    """EP-3: 提交 approved 决策成功；review_state→approved；review_version→1."""
    cvclf_id, _ = seeded_classification
    body = {
        "decision": "approved",
        "reviewer_id": "ops-tester",
        "expected_review_version": 0,
    }
    resp = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json=body,
        headers={"X-Reviewer-Id": "ops-tester"},
    )
    assert resp.status_code == 200, resp.text
    data = assert_success_envelope(resp.json())
    assert data["decision"] == "approved"
    assert data["reviewer_id"] == "ops-tester"
    # 验证 DB 落位
    async with session_factory() as session:
        row = (
            await session.execute(
                select(CoachVideoClassification).where(
                    CoachVideoClassification.id == cvclf_id
                )
            )
        ).scalar_one()
        assert row.review_state == "approved"
        assert row.review_version == 1
        assert row.last_decision_id is not None


@pytest.mark.asyncio
async def test_ep3_rejected_without_reason_returns_400(
    session_factory, client, seeded_classification
):
    """EP-3: decision=rejected 缺 reason_code 返回 400 REJECTED_REQUIRES_REASON."""
    cvclf_id, _ = seeded_classification
    body = {
        "decision": "rejected",
        "reviewer_id": "ops-tester",
        "expected_review_version": 0,
        # reason_code 故意省略
    }
    resp = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json=body,
        headers={"X-Reviewer-Id": "ops-tester"},
    )
    assert resp.status_code == 400, resp.text
    err = assert_error_envelope(resp.json(), code="REJECTED_REQUIRES_REASON")
    assert "reason_code" in err.get("message", "")


@pytest.mark.asyncio
async def test_ep3_rejected_with_reason_succeeds(
    session_factory, client, seeded_classification
):
    """EP-3: decision=rejected + reason_code 提交成功；review_state→rejected."""
    cvclf_id, _ = seeded_classification
    body = {
        "decision": "rejected",
        "reason_code": "quality_low",
        "note": "前 30 秒抖动严重",
        "reviewer_id": "ops-tester",
        "expected_review_version": 0,
    }
    resp = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json=body,
        headers={"X-Reviewer-Id": "ops-tester"},
    )
    assert resp.status_code == 200, resp.text
    data = assert_success_envelope(resp.json())
    assert data["decision"] == "rejected"
    assert data["reason_code"] == "quality_low"
    async with session_factory() as session:
        row = (
            await session.execute(
                select(CoachVideoClassification).where(
                    CoachVideoClassification.id == cvclf_id
                )
            )
        ).scalar_one()
        assert row.review_state == "rejected"


@pytest.mark.asyncio
async def test_ep3_version_conflict_returns_409(
    session_factory, client, seeded_classification
):
    """EP-3: expected_review_version 与服务端不一致 → 409 REVIEW_VERSION_CONFLICT."""
    cvclf_id, _ = seeded_classification
    # 提交一个非 0 的 expected_version；服务端当前是 0
    body = {
        "decision": "approved",
        "reviewer_id": "ops-tester",
        "expected_review_version": 99,
    }
    resp = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json=body,
        headers={"X-Reviewer-Id": "ops-tester"},
    )
    assert resp.status_code == 409, resp.text
    err = assert_error_envelope(resp.json(), code="REVIEW_VERSION_CONFLICT")
    details = err.get("details") or {}
    assert details.get("expected_version") == 99
    assert details.get("current_version") == 0


@pytest.mark.asyncio
async def test_ep3_review_not_pending_returns_409(
    session_factory, client, seeded_classification
):
    """EP-3: 已 approved 后再提决策 → 409 REVIEW_NOT_PENDING."""
    cvclf_id, _ = seeded_classification
    # 第一次 approved
    body = {
        "decision": "approved",
        "reviewer_id": "ops-tester",
        "expected_review_version": 0,
    }
    resp1 = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json=body,
        headers={"X-Reviewer-Id": "ops-tester"},
    )
    assert resp1.status_code == 200

    # 第二次再提 → 409 REVIEW_NOT_PENDING（review_state 已变 approved）
    body2 = {
        "decision": "rejected",
        "reason_code": "other",
        "note": "尝试覆盖",
        "reviewer_id": "ops-tester",
        "expected_review_version": 1,
    }
    resp2 = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json=body2,
        headers={"X-Reviewer-Id": "ops-tester"},
    )
    assert resp2.status_code == 409, resp2.text
    assert_error_envelope(resp2.json(), code="REVIEW_NOT_PENDING")


@pytest.mark.asyncio
async def test_ep3_invalid_reviewer_identity_returns_400(
    session_factory, client, seeded_classification
):
    """EP-3: header X-Reviewer-Id 与 body reviewer_id 不一致 → 400 INVALID_REVIEWER_IDENTITY."""
    cvclf_id, _ = seeded_classification
    body = {
        "decision": "approved",
        "reviewer_id": "ops-zhangwei",  # body
        "expected_review_version": 0,
    }
    resp = await client.post(
        f"/api/v1/content-reviews/{cvclf_id}/decisions",
        json=body,
        headers={"X-Reviewer-Id": "ops-lihua"},  # header 不一致
    )
    assert resp.status_code == 400, resp.text
    err = assert_error_envelope(resp.json(), code="INVALID_REVIEWER_IDENTITY")
    details = err.get("details") or {}
    assert details.get("header_value") == "ops-lihua"
    assert details.get("body_value") == "ops-zhangwei"


# ══════════════════════════════════════════════════════════════════════════
# EP-4: GET /content-reviews/stats
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ep4_stats_default_envelope(client):
    """EP-4: 统计接口默认返回成功信封 + 关键字段齐全."""
    now = datetime.now()
    from_ts = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    to_ts = now.isoformat()
    resp = await client.get(
        f"/api/v1/content-reviews/stats?from={from_ts}&to={to_ts}"
    )
    assert resp.status_code == 200, resp.text
    data = assert_success_envelope(resp.json())
    for field in ("from", "to", "total", "approved", "rejected", "approval_rate"):
        assert field in data, f"stats payload missing {field!r}"


@pytest.mark.asyncio
async def test_ep4_stats_invalid_window_returns_400(client):
    """EP-4: 缺少必填 from/to 返回 400/422."""
    resp = await client.get("/api/v1/content-reviews/stats")
    assert resp.status_code in (400, 422)
    body = resp.json()
    assert_error_envelope(body)


# ══════════════════════════════════════════════════════════════════════════
# EP-5: GET / PATCH /admin/review-gate
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ep5a_review_gate_get_returns_default_enabled(client):
    """EP-5a: 默认 enabled=true（"严格审核门"）."""
    resp = await client.get("/api/v1/admin/review-gate")
    assert resp.status_code == 200, resp.text
    data = assert_success_envelope(resp.json())
    assert isinstance(data["enabled"], bool)
    # 默认为 true（迁移 0021 已 INSERT enabled=true 行）
    assert data["enabled"] is True


@pytest.mark.asyncio
async def test_ep5b_review_gate_patch_toggles(session_factory, client):
    """EP-5b: PATCH 切换为 false → 再切回 true，DB 实时反映."""
    body_off = {
        "enabled": False,
        "operator_id": "ops-admin",
        "reason": "T014 contract test rollback drill",
    }
    resp_off = await client.patch("/api/v1/admin/review-gate", json=body_off)
    assert resp_off.status_code == 200, resp_off.text
    data_off = assert_success_envelope(resp_off.json())
    assert data_off["enabled"] is False

    # 切回 true，恢复默认（避免污染其它测试）
    body_on = {
        "enabled": True,
        "operator_id": "ops-admin",
        "reason": "T014 contract test rollback drill — restore",
    }
    resp_on = await client.patch("/api/v1/admin/review-gate", json=body_on)
    assert resp_on.status_code == 200
    data_on = assert_success_envelope(resp_on.json())
    assert data_on["enabled"] is True


@pytest.mark.asyncio
async def test_ep5b_review_gate_patch_missing_operator_returns_400(client):
    """EP-5b: 请求体缺 operator_id → 400/422."""
    body = {"enabled": False}  # 缺 operator_id 与 reason
    resp = await client.patch("/api/v1/admin/review-gate", json=body)
    assert resp.status_code in (400, 422)
    err = assert_error_envelope(resp.json())
    assert err["code"] in {"REVIEW_GATE_INVALID_STATE", "VALIDATION_FAILED"}


# ══════════════════════════════════════════════════════════════════════════
# KB 抽取入口 3 个 review_state 阻断错误码
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_kb_extract_blocked_when_pending_review(
    session_factory, client, seeded_classification
):
    """KB 抽取入口：review_state=pending_review → 409 CONTENT_NOT_REVIEWED.

    需要先模拟"清洗已成功"才能让审核门成为唯一拦截因素，否则会被
    Feature-021 清洗门 CURATION_REQUIRED 提前拦下。
    """
    cvclf_id, cos_object_key = seeded_classification
    # 模拟一个 success 状态的 video_curation_job + preprocessing_job
    async with session_factory() as session:
        prep_job = VideoPreprocessingJob(
            id=uuid.uuid4(),
            cos_object_key=cos_object_key,
            status="success",
        )
        session.add(prep_job)
        await session.flush()
        cur_job = VideoCurationJob(
            id=uuid.uuid4(),
            cos_object_key=cos_object_key,
            coach_video_classification_id=cvclf_id,
            preprocessing_job_id=prep_job.id,
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
        # 让 cvclf 持有 last_curation_job_id 满足"清洗已成功"门
        await session.execute(
            update(CoachVideoClassification)
            .where(CoachVideoClassification.id == cvclf_id)
            .values(last_curation_job_id=cur_job.id)
        )
        await session.commit()

    # 现在提交 KB 抽取 → 应被 CONTENT_NOT_REVIEWED 拦截
    resp = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    assert resp.status_code == 409, resp.text
    err = assert_error_envelope(resp.json(), code="CONTENT_NOT_REVIEWED")
    assert err.get("details", {}).get("cos_object_key") == cos_object_key


@pytest.mark.asyncio
async def test_kb_extract_blocked_when_rejected(
    session_factory, client, seeded_classification
):
    """KB 抽取入口：review_state=rejected → 409 CONTENT_REVIEW_REJECTED."""
    cvclf_id, cos_object_key = seeded_classification

    async with session_factory() as session:
        # 模拟清洗成功
        prep_job = VideoPreprocessingJob(
            id=uuid.uuid4(), cos_object_key=cos_object_key, status="success",
        )
        session.add(prep_job)
        await session.flush()
        cur_job = VideoCurationJob(
            id=uuid.uuid4(),
            cos_object_key=cos_object_key,
            coach_video_classification_id=cvclf_id,
            preprocessing_job_id=prep_job.id,
            curation_rubric_version="v1",
            status="success",
            total_segment_count=5, accepted_segment_count=5,
            rejected_segment_count=0, uncertain_segment_count=0,
            total_duration_seconds=180.0, accepted_duration_seconds=180.0,
            accepted_duration_ratio=1.0, low_quality=False,
            audio_unavailable=False, short_video=False,
            completed_at=datetime.now(),
        )
        session.add(cur_job)
        await session.flush()
        # 直接把 review_state 改为 rejected（绕过 EP-3 提交，纯 DB 模拟）
        await session.execute(
            update(CoachVideoClassification)
            .where(CoachVideoClassification.id == cvclf_id)
            .values(
                last_curation_job_id=cur_job.id,
                review_state="rejected",
                review_version=1,
            )
        )
        await session.commit()

    resp = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    assert resp.status_code == 409, resp.text
    assert_error_envelope(resp.json(), code="CONTENT_REVIEW_REJECTED")


@pytest.mark.asyncio
async def test_kb_extract_blocked_when_stale(
    session_factory, client, seeded_classification
):
    """KB 抽取入口：review_state=stale → 409 CONTENT_REVIEW_STALE."""
    cvclf_id, cos_object_key = seeded_classification

    async with session_factory() as session:
        prep_job = VideoPreprocessingJob(
            id=uuid.uuid4(), cos_object_key=cos_object_key, status="success",
        )
        session.add(prep_job)
        await session.flush()
        cur_job = VideoCurationJob(
            id=uuid.uuid4(),
            cos_object_key=cos_object_key,
            coach_video_classification_id=cvclf_id,
            preprocessing_job_id=prep_job.id,
            curation_rubric_version="v1",
            status="success",
            total_segment_count=5, accepted_segment_count=5,
            rejected_segment_count=0, uncertain_segment_count=0,
            total_duration_seconds=180.0, accepted_duration_seconds=180.0,
            accepted_duration_ratio=1.0, low_quality=False,
            audio_unavailable=False, short_video=False,
            completed_at=datetime.now(),
        )
        session.add(cur_job)
        await session.flush()
        await session.execute(
            update(CoachVideoClassification)
            .where(CoachVideoClassification.id == cvclf_id)
            .values(
                last_curation_job_id=cur_job.id,
                review_state="stale",
                review_version=2,
            )
        )
        await session.commit()

    resp = await client.post(
        "/api/v1/tasks/kb-extraction",
        json={"cos_object_key": cos_object_key, "force": False},
    )
    assert resp.status_code == 409, resp.text
    assert_error_envelope(resp.json(), code="CONTENT_REVIEW_STALE")
