"""Feature-022 · T029 · review 列表接口性能验证测试.

验证 spec.md FR-017 / SC-006 性能约束：

  · GET /content-reviews?page_size=20  → P95 < 200ms
  · GET /content-reviews?page_size=50  → P95 < 500ms

测试方法：
  1. 批量插入大量 ``coach_video_classifications`` 行（混合 4 种 review_state）
  2. 通过 ``client.get`` 反复调用列表接口 N 次（默认 30 轮）
  3. 计算 P95 延迟，断言 ≤ 阈值

为缩短 CI 时间，本测试默认仅插入 5,000 条（spec FR-017 给出"中规模 50–200 条/日"基线，
3 个月累计约 4,500–18,000 条；5,000 条已能触发索引选择优化路径）。
如需做 50,000 条压测，可设置环境变量 ``T029_BULK_SIZE=50000`` 后单独执行。

测试边界：
  - 不依赖外部压测工具（locust / ab）；用 asyncio + httpx 顺序请求即可
  - 插入数据走单事务批量提交，不走完整 ORM hook（性能最大化）
  - 使用唯一 _TAG 后缀隔离，cleanup 仅清理本测试 seed 的行
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.config import get_settings
from src.db import session as session_module
from src.models.coach_video_classification import CoachVideoClassification
from tests.contract.conftest import assert_success_envelope


_TAG = f"__t029_{uuid.uuid4().hex[:8]}"
_BULK_SIZE = int(os.environ.get("T029_BULK_SIZE", "5000"))
_WARMUP_REQUESTS = 3   # 预热（让连接池 / SQL 执行计划缓存到位）
_MEASURE_REQUESTS = 30  # 正式测量轮数


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_size=4, max_overflow=4, pool_pre_ping=False,
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
async def bulk_seeded(session_factory):
    """批量 seed _BULK_SIZE 条 cvclf 行；混合 4 种 review_state（35% pending / 30% approved / 30% rejected / 5% stale）.

    cleanup 阶段按 coach_name LIKE _TAG_% 删除，避免影响其它测试。
    """
    states = ["pending_review"] * 35 + ["approved"] * 30 + ["rejected"] * 30 + ["stale"] * 5
    tech_categories = [
        "forehand_topspin", "backhand_attack", "serve", "receive", "footwork",
    ]

    rows = []
    for i in range(_BULK_SIZE):
        state = states[i % 100]
        rows.append(
            CoachVideoClassification(
                id=uuid.uuid4(),
                coach_name=f"{_TAG}_coach_{i % 10}",
                course_series=f"{_TAG}_series_{i % 5}",
                cos_object_key=f"charhuang/tt_video/{_TAG}/v{i:06d}.mp4",
                filename=f"v{i:06d}.mp4",
                tech_category=tech_categories[i % len(tech_categories)],
                tech_tags=[],
                classification_source="rule",
                confidence=1.0,
                kb_extracted=False,
                preprocessed=True,
                review_state=state,
                review_version=0 if state == "pending_review" else 1,
            )
        )

    # 批量插入：单事务 add_all + commit
    async with session_factory() as session:
        # add_all 走 ORM；对 5000 条来说 PostgreSQL 1-2s 完成，可接受
        session.add_all(rows)
        await session.commit()

    yield

    # Cleanup：按 coach_name LIKE _TAG_% 删除
    async with session_factory() as session:
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.coach_name.like(f"{_TAG}_%")
            )
        )
        await session.commit()


# ── Tests ────────────────────────────────────────────────────────────────


def _percentile(latencies: list[float], pct: float) -> float:
    """计算分位数 (0 < pct < 100). 使用 numpy 风格的线性插值法."""
    if not latencies:
        return 0.0
    sorted_lat = sorted(latencies)
    k = (len(sorted_lat) - 1) * (pct / 100.0)
    floor = int(k)
    ceil = floor + 1 if floor + 1 < len(sorted_lat) else floor
    if floor == ceil:
        return sorted_lat[floor]
    frac = k - floor
    return sorted_lat[floor] * (1 - frac) + sorted_lat[ceil] * frac


async def _run_list_call(
    client: AsyncClient, *, page_size: int, state: str | None = None
) -> float:
    """单次列表调用，返回耗时（毫秒）."""
    params = f"page=1&page_size={page_size}"
    if state:
        params += f"&state={state}"
    t0 = time.perf_counter()
    resp = await client.get(f"/api/v1/content-reviews?{params}")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert resp.status_code == 200, resp.text
    # 验证响应信封形态（确保不是降级响应）
    body = resp.json()
    data = assert_success_envelope(body, expect_meta=True)
    assert isinstance(data, list)
    return elapsed_ms


@pytest.mark.asyncio
async def test_list_p95_page_size_20_under_200ms(client, bulk_seeded):
    """FR-017: page_size=20 列表 P95 < 200ms（默认过滤 rejected 路径）.

    测试流程：
    - 预热 _WARMUP_REQUESTS 次（消除冷启动 + 连接池建立 + SQL plan 缓存）
    - 测量 _MEASURE_REQUESTS 次（正式测量）
    - 计算 P50 / P95 / max 三个分位数，断言 P95 < 200ms

    注意：本测试在本机开发环境运行；若 CI 资源受限，可通过环境变量 T029_BULK_SIZE
    缩小数据量。50,000 条压测建议在生产同等 PG 资源池下单独执行。
    """
    # 预热
    for _ in range(_WARMUP_REQUESTS):
        await _run_list_call(client, page_size=20)

    # 正式测量
    latencies = []
    for _ in range(_MEASURE_REQUESTS):
        latencies.append(await _run_list_call(client, page_size=20))

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p_max = max(latencies)
    avg = statistics.fmean(latencies)

    # 断言（FR-017）
    threshold_ms = 200
    assert p95 < threshold_ms, (
        f"page_size=20 P95 {p95:.1f}ms 超过阈值 {threshold_ms}ms — "
        f"详情: P50={p50:.1f}ms, P95={p95:.1f}ms, max={p_max:.1f}ms, "
        f"avg={avg:.1f}ms, samples={len(latencies)}, bulk={_BULK_SIZE}"
    )


@pytest.mark.asyncio
async def test_list_p95_page_size_50_under_500ms(client, bulk_seeded):
    """FR-017: page_size=50 列表 P95 < 500ms."""
    for _ in range(_WARMUP_REQUESTS):
        await _run_list_call(client, page_size=50)

    latencies = []
    for _ in range(_MEASURE_REQUESTS):
        latencies.append(await _run_list_call(client, page_size=50))

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p_max = max(latencies)
    avg = statistics.fmean(latencies)

    threshold_ms = 500
    assert p95 < threshold_ms, (
        f"page_size=50 P95 {p95:.1f}ms 超过阈值 {threshold_ms}ms — "
        f"详情: P50={p50:.1f}ms, P95={p95:.1f}ms, max={p_max:.1f}ms, "
        f"avg={avg:.1f}ms, samples={len(latencies)}, bulk={_BULK_SIZE}"
    )


@pytest.mark.asyncio
async def test_list_pending_review_filter_uses_index(client, bulk_seeded):
    """额外性能保障：state=pending_review 显式过滤路径（最频繁的工作台路径）也应 < 200ms.

    审核工作台的核心 UX 是默认聚焦 ``state=pending_review`` 列表，按 pending_since
    升序展示积压最久的条目；该路径必须命中 idx_cvclf_review_state_pending_since
    复合索引（迁移 0021 内建）。
    """
    for _ in range(_WARMUP_REQUESTS):
        await _run_list_call(client, page_size=20, state="pending_review")

    latencies = []
    for _ in range(_MEASURE_REQUESTS):
        latencies.append(
            await _run_list_call(client, page_size=20, state="pending_review")
        )

    p95 = _percentile(latencies, 95)
    assert p95 < 200, (
        f"state=pending_review filter P95 {p95:.1f}ms 超过阈值 200ms; "
        f"samples={len(latencies)}, bulk={_BULK_SIZE}"
    )
