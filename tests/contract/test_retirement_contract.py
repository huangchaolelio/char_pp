"""Feature-017 — 已下线接口哨兵路由合约测试.

按 :data:`~src.api.routers._retired.RETIREMENT_LEDGER` 参数化，为每条下线端点断言：

- HTTP 状态码为 **404**
- 响应体为合格 ``ErrorEnvelope``
- ``error.code == "ENDPOINT_RETIRED"``
- ``error.details.successor`` 与台账完全一致（字符串或字符串列表）
- ``error.details.migration_note`` 与台账完全一致

所有请求均在**独立的测试 app** 上发起，不依赖主 app，便于本阶段（阶段 2）在未改造
主 app 前即可红→绿.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.errors import register_exception_handlers
from src.api.routers._retired import RETIREMENT_LEDGER, RetiredEndpoint, build_retired_router
from tests.contract.conftest import assert_error_envelope


# ── 测试 app（本文件内独立） ──────────────────────────────────────────────
@pytest.fixture(scope="module")
def retired_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    # 空 prefix 挂载——RETIREMENT_LEDGER.path 本身已含 /api/v1
    app.include_router(build_retired_router())
    return TestClient(app)


# ── 为 cos_object_key:path 类通配路径准备一个示例值 ────────────────────────
def _sample_path_value(path_with_placeholder: str) -> str:
    """将 ``/videos/classifications/{cos_object_key:path}`` 替换为可访问的具体示例."""
    if "{cos_object_key:path}" in path_with_placeholder:
        return path_with_placeholder.replace(
            "{cos_object_key:path}", "charhuang/tt_video/%E4%B9%92%E4%B9%93%E7%90%83/a.mp4",
        )
    # 其他 {xxx} 占位符（目前台账无此情况，预留）
    return path_with_placeholder


# ── 参数化覆盖所有台账条目 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "endpoint",
    RETIREMENT_LEDGER,
    ids=[f"{e.method}_{e.path}" for e in RETIREMENT_LEDGER],
)
def test_retired_endpoint_returns_404_with_envelope(
    retired_client: TestClient, endpoint: RetiredEndpoint,
) -> None:
    path = _sample_path_value(endpoint.path)
    # 选择不易触发 Pydantic 校验的空请求体/查询
    method = endpoint.method.lower()
    client_method = getattr(retired_client, method)

    kwargs: dict = {}
    if endpoint.method in ("POST", "PATCH", "PUT"):
        kwargs["json"] = {}

    resp = client_method(path, **kwargs)

    # ── 状态码 ────────────────────────────────────────────────────────────
    assert resp.status_code == 404, (
        f"{endpoint.method} {path} expected 404, got {resp.status_code}; body={resp.text}"
    )

    # ── 信封结构 ──────────────────────────────────────────────────────────
    err = assert_error_envelope(resp.json(), code="ENDPOINT_RETIRED")

    # ── details 完全一致 ──────────────────────────────────────────────────
    details = err["details"]
    assert isinstance(details, dict)

    expected_successor: str | list[str]
    if isinstance(endpoint.successor, tuple):
        expected_successor = list(endpoint.successor)
    else:
        expected_successor = endpoint.successor

    assert details["successor"] == expected_successor, (
        f"successor mismatch for {endpoint.method} {endpoint.path}: "
        f"got {details['successor']!r}, expected {expected_successor!r}"
    )
    assert details["migration_note"] == endpoint.migration_note


# ── 台账完整性与去重 ──────────────────────────────────────────────────────
class TestLedgerIntegrity:
    def test_no_duplicate_method_path(self) -> None:
        seen = set()
        for ep in RETIREMENT_LEDGER:
            key = (ep.method, ep.path)
            assert key not in seen, f"duplicate retirement entry: {key}"
            seen.add(key)

    def test_all_paths_start_with_api_v1(self) -> None:
        for ep in RETIREMENT_LEDGER:
            assert ep.path.startswith("/api/v1/"), f"invalid path: {ep.path}"

    def test_all_methods_uppercase(self) -> None:
        allowed = {"GET", "POST", "PATCH", "DELETE", "PUT"}
        for ep in RETIREMENT_LEDGER:
            assert ep.method in allowed, f"invalid method: {ep.method}"

    def test_successor_non_empty(self) -> None:
        for ep in RETIREMENT_LEDGER:
            if isinstance(ep.successor, tuple):
                assert len(ep.successor) >= 1
                for s in ep.successor:
                    assert isinstance(s, str) and s.startswith("/api/v1/")
            else:
                assert isinstance(ep.successor, str) and ep.successor.startswith("/api/v1/")

    def test_ledger_has_seven_entries(self) -> None:
        """锁定 Feature-017 首发数量；未来追加时需同步更新该断言."""
        assert len(RETIREMENT_LEDGER) == 7


# ── 区分 ENDPOINT_RETIRED 与完全未匹配路由 ────────────────────────────────
class TestUnknownRouteDistinction:
    def test_unknown_path_is_not_retired(self, retired_client: TestClient) -> None:
        """完全不存在的路径应由 FastAPI 默认 404 响应，不应被哨兵路由捕获."""
        resp = retired_client.get("/api/v1/this-path-never-existed")
        assert resp.status_code == 404
        body = resp.json()
        # 默认 FastAPI 响应结构为 {"detail": "Not Found"}，不含 success/error 字段
        # 本测试仅断言它不是 ENDPOINT_RETIRED 误伤
        if "error" in body:
            assert body["error"].get("code") != "ENDPOINT_RETIRED"
