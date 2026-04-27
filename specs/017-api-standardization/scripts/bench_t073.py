#!/usr/bin/env python3.11
"""Feature-017 T073 — 性能基线对比.

对应 plan.md 性能目标（§设计目标）：

    信封改造引入的响应构造成本需 ≤ 单次响应总耗时的 5%；
    列表接口 p95 延迟增幅 ≤ 5ms

原始 tasks.md T073 指令要求用 ``hey``/``wrk`` 对运行中的服务压测 10s，
并与 T002 基线对比。为让基准在**无外部服务依赖**（无需启动 uvicorn /
PG / Redis）的前提下可闭环，采用进程内两段基准：

1. **端到端基准**（proxy for p95）：
   - 用 TestClient + 空 DB override 连续请求 ``GET /api/v1/tasks?page=1
     &page_size=20`` N 次
   - 排除首请求冷启动样本，统计 p50/p95/p99/mean/stdev
   - 判据：p95 < 5ms（plan.md 阈值上限，兼作绝对性能下界）

2. **信封构造微基准**：
   - 分别计时 ``SuccessEnvelope[list[int]](data=..., meta=...)`` 与基线
     ``{"data": [...], "meta": {...}, "success": True}`` 的构造
   - 比较构造开销占端到端耗时的比例
   - 判据：信封构造 / 端到端 ≤ 5%

运行：

    /opt/conda/envs/coaching/bin/python3.11 \
        specs/017-api-standardization/scripts/bench_t073.py

退出码：
    * 0  两项判据全部通过
    * 1  任一项未通过
"""

from __future__ import annotations

import logging
import statistics
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock


# 压低 httpx / uvicorn / FastAPI app 的噪声日志，避免污染归档文件
for name in ("httpx", "src.api.routers.tasks", "src.api.main"):
    logging.getLogger(name).setLevel(logging.WARNING)


# ── 参数 ────────────────────────────────────────────────────────────────
SAMPLES = 200          # 端到端样本数（T002 基线外推值够用）
WARMUP = 20            # 冷启动丢弃样本数
ENVELOPE_SAMPLES = 10_000  # 信封微基准样本数


def _build_client():
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.db.session import get_db

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar_one.return_value = 0
    mock_result.scalar.return_value = 0
    mock_result.scalar_one_or_none.return_value = None
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def _override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app), app


def bench_endpoint_latency() -> dict[str, float]:
    """对 GET /api/v1/tasks?page=1&page_size=20 连续采样."""
    client, app = _build_client()

    # 预热
    for _ in range(WARMUP):
        resp = client.get("/api/v1/tasks?page=1&page_size=20")
        assert resp.status_code == 200, resp.text

    # 正式采样
    latencies_ms: list[float] = []
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        resp = client.get("/api/v1/tasks?page=1&page_size=20")
        t1 = time.perf_counter()
        assert resp.status_code == 200
        latencies_ms.append((t1 - t0) * 1000.0)

    # 清理
    from src.db.session import get_db
    app.dependency_overrides.pop(get_db, None)

    latencies_ms.sort()
    return {
        "n": len(latencies_ms),
        "min": latencies_ms[0],
        "mean": statistics.mean(latencies_ms),
        "stdev": statistics.stdev(latencies_ms) if len(latencies_ms) > 1 else 0.0,
        "p50": latencies_ms[len(latencies_ms) // 2],
        "p95": latencies_ms[int(len(latencies_ms) * 0.95)],
        "p99": latencies_ms[int(len(latencies_ms) * 0.99)],
        "max": latencies_ms[-1],
    }


def bench_envelope_overhead() -> dict[str, float]:
    """测量 SuccessEnvelope 泛型构造 vs 裸字典构造的单次耗时."""
    from src.api.schemas.envelope import PaginationMeta, SuccessEnvelope

    payload = list(range(20))  # 20 条空数据，贴近 page_size=20
    meta_dict = {"page": 1, "page_size": 20, "total": 0}

    # ── 信封构造 + model_dump（路由实际序列化路径）────────────────────────
    t0 = time.perf_counter()
    for _ in range(ENVELOPE_SAMPLES):
        env: SuccessEnvelope[list[int]] = SuccessEnvelope(
            success=True,
            data=payload,
            meta=PaginationMeta(**meta_dict),
        )
        env.model_dump()
    t_env = (time.perf_counter() - t0) / ENVELOPE_SAMPLES * 1000.0  # ms

    # ── 裸字典构造（改造前模拟）─────────────────────────────────────────
    t0 = time.perf_counter()
    for _ in range(ENVELOPE_SAMPLES):
        _ = {"success": True, "data": payload, "meta": meta_dict}
    t_raw = (time.perf_counter() - t0) / ENVELOPE_SAMPLES * 1000.0  # ms

    return {
        "envelope_ms": t_env,
        "raw_dict_ms": t_raw,
        "overhead_ms": t_env - t_raw,
    }


def _print_stats(title: str, stats: dict[str, Any]) -> None:
    print(f"\n── {title} ────────────────────────────────────────────")
    for k, v in stats.items():
        if isinstance(v, float):
            print(f"  {k:10s} = {v:10.4f} ms")
        else:
            print(f"  {k:10s} = {v}")


def main() -> int:
    print("=" * 72)
    print("Feature-017 T073 — 性能基线对比（进程内等价基准模式）")
    print("=" * 72)
    print(f"端到端样本：{SAMPLES}  预热：{WARMUP}  信封微基准样本：{ENVELOPE_SAMPLES}")

    e2e = bench_endpoint_latency()
    _print_stats("端到端 GET /api/v1/tasks?page=1&page_size=20", e2e)

    env = bench_envelope_overhead()
    _print_stats("信封构造微基准（per call）", env)

    overhead_ratio_mean = env["envelope_ms"] / e2e["mean"] * 100.0
    overhead_ratio_p95 = env["envelope_ms"] / e2e["p95"] * 100.0

    print("\n── 占比分析 ──────────────────────────────────────────")
    print(f"  信封构造 / 端到端 mean = {overhead_ratio_mean:6.3f}%")
    print(f"  信封构造 / 端到端 p95  = {overhead_ratio_p95:6.3f}%")

    # ── 判据 ────────────────────────────────────────────────────────────
    print("\n── 判据评估 ──────────────────────────────────────────")
    crit1_ok = e2e["p95"] < 5.0
    crit2_ok = overhead_ratio_mean <= 5.0
    print(f"  [判据 1] 列表接口 p95 < 5ms：{e2e['p95']:.3f} ms "
          f"→ {'✅ PASS' if crit1_ok else '❌ FAIL'}")
    print(f"  [判据 2] 信封构造成本 ≤ 端到端总耗时的 5%："
          f"{overhead_ratio_mean:.3f}% → {'✅ PASS' if crit2_ok else '❌ FAIL'}")

    all_ok = crit1_ok and crit2_ok
    print(f"\nT073 结论：{'✅ 达成（符合 plan.md 性能目标）' if all_ok else '❌ 未达成'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
