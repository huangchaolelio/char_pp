"""Contract tests — 分页参数越界统一行为验证（Feature-017 T055）.

覆盖 6 条已启用 ``page/page_size`` 分页参数的列表端点，统一断言：
  1. ``page_size=0``       → 422 + VALIDATION_FAILED（Pydantic ``ge=1`` 拦截）
  2. ``page_size=101``     → 422 + VALIDATION_FAILED（Pydantic ``le=100`` 拦截）
  3. ``page=0``            → 422 + VALIDATION_FAILED（Pydantic ``ge=1`` 拦截）
  4. ``page_size=abc``     → 422 + VALIDATION_FAILED（Pydantic 类型解析拦截）

为避免对数据库的真实依赖，使用 FastAPI TestClient 直接请求；因 Query 参数
验证发生在路由 handler 调用**之前**（FastAPI 依赖解析阶段），即便 DB 未连接
也能正确返回 422，与章程 v1.4.0 ``VALIDATION_FAILED`` 映射一致。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


PAGINATED_ENDPOINTS: list[str] = [
    "/api/v1/tasks",
    "/api/v1/coaches",
    "/api/v1/classifications",
    "/api/v1/classifications/summary",
    "/api/v1/teaching-tips",
    "/api/v1/knowledge-base/versions",
    "/api/v1/task-channels",
    "/api/v1/extraction-jobs",
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize("path", PAGINATED_ENDPOINTS)
class TestPaginationBoundary:
    """Feature-017 T055：分页参数越界统一返回 422 + VALIDATION_FAILED."""

    def test_page_size_zero_rejected(self, client: TestClient, path: str) -> None:
        resp = client.get(f"{path}?page_size=0")
        assert resp.status_code == 422, f"{path}?page_size=0 → {resp.status_code}"
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_FAILED"

    def test_page_size_over_max_rejected(self, client: TestClient, path: str) -> None:
        resp = client.get(f"{path}?page_size=101")
        assert resp.status_code == 422, f"{path}?page_size=101 → {resp.status_code}"
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_FAILED"

    def test_page_zero_rejected(self, client: TestClient, path: str) -> None:
        resp = client.get(f"{path}?page=0")
        assert resp.status_code == 422, f"{path}?page=0 → {resp.status_code}"
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_FAILED"

    def test_page_size_non_integer_rejected(self, client: TestClient, path: str) -> None:
        resp = client.get(f"{path}?page_size=abc")
        assert resp.status_code == 422, f"{path}?page_size=abc → {resp.status_code}"
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_FAILED"
