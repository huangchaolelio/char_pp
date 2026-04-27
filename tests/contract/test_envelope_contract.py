"""Feature-017 — 通用响应信封契约测试（独立于业务路由）.

通过构建一个**仅在测试内存中存在**的最小 FastAPI app（不引入主 app 路由），
验证 ``assert_success_envelope`` / ``assert_error_envelope`` 夹具的三条核心断言链路：

1. ``/test/envelope-ok`` → 返回合格 ``SuccessEnvelope``（含分页 meta）
2. ``/test/envelope-error`` → 抛 ``AppException``，返回合格 ``ErrorEnvelope``
3. ``/test/envelope-bad-page`` → 触发 Pydantic 请求校验失败，返回 ``VALIDATION_FAILED`` 错误信封

**注意**: 本测试文件**不修改主 app**，这些 ``/test/*`` 路由仅在测试进程内存中存在。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from src.api.errors import AppException, ErrorCode, register_exception_handlers
from src.api.schemas.envelope import SuccessEnvelope, ok, page
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


# ── 构建最小测试 app ───────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def envelope_client() -> TestClient:
    """独立的契约 sandbox；不污染主 app."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/test/envelope-ok", response_model=SuccessEnvelope[list[dict]])
    async def _envelope_ok() -> SuccessEnvelope[list[dict]]:
        return page(
            [{"id": 1, "name": "x"}, {"id": 2, "name": "y"}],
            page=1,
            page_size=20,
            total=2,
        )

    @app.get("/test/envelope-ok-single", response_model=SuccessEnvelope[dict])
    async def _envelope_ok_single() -> SuccessEnvelope[dict]:
        return ok({"id": 42, "name": "single"})

    @app.get("/test/envelope-error")
    async def _envelope_error() -> dict[str, str]:
        raise AppException(
            ErrorCode.TASK_NOT_FOUND,
            details={"task_id": "deadbeef"},
        )

    @app.get("/test/envelope-bad-page")
    async def _envelope_bad_page(
        page_size: int = Query(..., ge=1, le=100),
    ) -> dict[str, int]:
        return {"page_size": page_size}

    return TestClient(app)


# ── 成功信封契约 ──────────────────────────────────────────────────────────
class TestSuccessEnvelopeContract:
    def test_list_with_meta(self, envelope_client: TestClient) -> None:
        resp = envelope_client.get("/test/envelope-ok")
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0] == {"id": 1, "name": "x"}

    def test_single_without_meta(self, envelope_client: TestClient) -> None:
        resp = envelope_client.get("/test/envelope-ok-single")
        assert resp.status_code == 200
        body = resp.json()
        data = assert_success_envelope(body, expect_meta=False)
        assert data == {"id": 42, "name": "single"}
        assert body["meta"] is None


# ── 错误信封契约 ──────────────────────────────────────────────────────────
class TestErrorEnvelopeContract:
    def test_app_exception_renders_error_envelope(
        self, envelope_client: TestClient,
    ) -> None:
        resp = envelope_client.get("/test/envelope-error")
        assert resp.status_code == 404
        err = assert_error_envelope(resp.json(), code="TASK_NOT_FOUND")
        assert err["details"] == {"task_id": "deadbeef"}
        assert "任务" in err["message"]

    def test_validation_failure_renders_validation_error(
        self, envelope_client: TestClient,
    ) -> None:
        # page_size=999 超出 le=100，触发 RequestValidationError
        resp = envelope_client.get("/test/envelope-bad-page", params={"page_size": 999})
        assert resp.status_code == 422
        err = assert_error_envelope(resp.json(), code="VALIDATION_FAILED")
        # details 至少含 field 或 value 信息
        details = err["details"]
        assert isinstance(details, dict)

    def test_missing_required_query_also_validates(
        self, envelope_client: TestClient,
    ) -> None:
        resp = envelope_client.get("/test/envelope-bad-page")
        assert resp.status_code == 422
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")


# ── 断言辅助函数自身的负向测试（防护夹具正确性） ──────────────────────────
class TestAssertHelpersNegative:
    def test_assert_success_rejects_error_envelope(self) -> None:
        import pytest as _pytest
        with _pytest.raises(AssertionError):
            assert_success_envelope({"success": False, "error": {"code": "X", "message": "y"}})

    def test_assert_error_rejects_success_envelope(self) -> None:
        import pytest as _pytest
        with _pytest.raises(AssertionError):
            assert_error_envelope({"success": True, "data": {}, "meta": None})

    def test_assert_error_rejects_wrong_code(self) -> None:
        import pytest as _pytest
        with _pytest.raises(AssertionError):
            assert_error_envelope(
                {"success": False, "error": {"code": "TASK_NOT_FOUND", "message": "x"}},
                code="COACH_NOT_FOUND",
            )
