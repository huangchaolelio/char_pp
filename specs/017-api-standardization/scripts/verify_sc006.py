#!/usr/bin/env python3.11
"""Feature-017 T049 — 手工验证 SC-006：8 条主要业务端点信封结构.

原始任务目标（见 specs/017-api-standardization/tasks.md T049）：

```bash
for path in /api/v1/tasks /api/v1/coaches /api/v1/classifications \
            /api/v1/teaching-tips /api/v1/extraction-jobs \
            /api/v1/knowledge-base/versions /api/v1/standards \
            /api/v1/task-channels; do
  curl -s http://localhost:8080$path | python -c "
    import sys,json; b=json.loads(sys.stdin.read());
    assert 'success' in b and isinstance(b['success'],bool)
  "
done
```

为降低对外部运行态（PostgreSQL + Redis + Celery）的强依赖，本脚本用
FastAPI ``TestClient`` + 轻量 mock 等价验证：只校验响应体信封结构，
不涉及任何真实数据写入。

运行：

    /opt/conda/envs/coaching/bin/python3.11 \
        specs/017-api-standardization/scripts/verify_sc006.py

退出码：
    * 0  8/8 端点全部符合 Feature-017 信封结构
    * 1  任一端点未返回合格信封
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

# ── 被测路径清单（与 tasks.md T049 严格一致）──────────────────────────────
ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/api/v1/tasks"),
    ("GET", "/api/v1/coaches"),
    ("GET", "/api/v1/classifications"),
    ("GET", "/api/v1/teaching-tips"),
    ("GET", "/api/v1/extraction-jobs"),
    ("GET", "/api/v1/knowledge-base/versions"),
    ("GET", "/api/v1/standards"),
    ("GET", "/api/v1/task-channels"),
]


def _build_empty_db_client() -> TestClient:
    """构造 TestClient 并注入空 DB override，避免真实 PG 连接."""
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
    return TestClient(app)


def _assert_envelope(path: str, body: dict[str, Any]) -> tuple[bool, str]:
    """校验 Feature-017 信封结构：顶层 success 布尔 + data/meta 或 error 互斥."""
    if not isinstance(body, dict):
        return False, f"响应非 JSON 对象：{type(body).__name__}"
    if "success" not in body:
        return False, "缺少顶层 success 字段"
    if not isinstance(body["success"], bool):
        return False, f"success 字段非 bool：{type(body['success']).__name__}"
    if body["success"]:
        if "data" not in body:
            return False, "success=True 但缺少 data 字段"
        if "error" in body:
            return False, "success=True 不得出现 error 字段"
        return True, f"SUCCESS data={type(body['data']).__name__} meta={body.get('meta')!r}"
    else:
        if "error" not in body:
            return False, "success=False 但缺少 error 字段"
        err = body["error"]
        if not isinstance(err, dict) or "code" not in err or "message" not in err:
            return False, f"error 结构不合法：{err!r}"
        return True, f"ERROR code={err['code']}"


def main() -> int:
    print("=" * 72)
    print("Feature-017 T049 — SC-006 手工验证（TestClient 等价验证模式）")
    print("=" * 72)

    # task_channels 需要 mock 掉 Redis 依赖（get_snapshot 读 Redis 实时指标）
    with patch("src.api.routers.task_channels.TaskChannelService") as SvcCls:
        from src.models.analysis_task import TaskType
        from src.services.task_channel_service import ChannelLiveSnapshot

        async def _mock_snap(_db, tt: TaskType):
            return ChannelLiveSnapshot(
                task_type=tt,
                queue_capacity=5,
                concurrency=1,
                current_pending=0,
                current_processing=0,
                remaining_slots=5,
                enabled=True,
                recent_completion_rate_per_min=0.0,
            )

        SvcCls.return_value.get_snapshot = AsyncMock(side_effect=_mock_snap)

        client = _build_empty_db_client()

        results: list[tuple[str, int, bool, str]] = []
        for method, path in ENDPOINTS:
            try:
                resp = client.request(method, path)
                status = resp.status_code
                body = resp.json()
                ok, note = _assert_envelope(path, body)
            except Exception as e:  # noqa: BLE001
                status = -1
                ok = False
                note = f"异常：{type(e).__name__}: {e}"
            results.append((path, status, ok, note))

        # 清理 override
        from src.api.main import app
        from src.db.session import get_db

        app.dependency_overrides.pop(get_db, None)

    # ── 输出报告 ─────────────────────────────────────────────────────────
    print()
    print(f"{'端点':<42} {'HTTP':>5} {'结果':>6}  说明")
    print("-" * 72)
    passed = 0
    for path, status, ok, note in results:
        flag = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"{path:<42} {status:>5} {flag:>6}  {note}")
    print("-" * 72)
    print(f"汇总：{passed}/{len(results)} 端点符合 Feature-017 信封结构")

    sc006_ok = passed == len(results)
    print(f"\nSC-006 结论：{'✅ 达成' if sc006_ok else '❌ 未达成'}")
    return 0 if sc006_ok else 1


if __name__ == "__main__":
    sys.exit(main())
