"""Feature-017 — ErrorCode / AppException / 异常处理器单元测试.

断言的核心不变量：
- 每个 ``ErrorCode`` 枚举值在 ``ERROR_STATUS_MAP`` 和 ``ERROR_DEFAULT_MESSAGE`` 中都有条目（防漏配）
- ``AppException(code)`` 的默认消息来自 ``ERROR_DEFAULT_MESSAGE.get(code)``
- ``AppException(code, message="..")`` 优先使用显式 message
- ``build_error_response()`` 产出的 JSON 结构符合 ``ErrorEnvelope`` 契约
- 三个异常处理器（AppException / RequestValidationError / Exception）注册完整
"""

from __future__ import annotations

import json
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from src.api.errors import (
    ERROR_DEFAULT_MESSAGE,
    ERROR_STATUS_MAP,
    AppException,
    ErrorCode,
    build_error_response,
    register_exception_handlers,
)


# ── ErrorCode 枚举完整性 ──────────────────────────────────────────────────
class TestErrorCodeIntegrity:
    def test_every_code_has_status_mapping(self) -> None:
        missing = [c for c in ErrorCode if c not in ERROR_STATUS_MAP]
        assert missing == [], f"ErrorCode without HTTP status mapping: {missing}"

    def test_every_code_has_default_message(self) -> None:
        missing = [c for c in ErrorCode if c not in ERROR_DEFAULT_MESSAGE]
        assert missing == [], f"ErrorCode without default message: {missing}"

    def test_status_map_only_contains_known_codes(self) -> None:
        unknown = [k for k in ERROR_STATUS_MAP if not isinstance(k, ErrorCode)]
        assert unknown == []

    def test_endpoint_retired_maps_to_404(self) -> None:
        """澄清决策 Q3：下线接口返回 404 而非 410."""
        assert ERROR_STATUS_MAP[ErrorCode.ENDPOINT_RETIRED] == HTTPStatus.NOT_FOUND

    def test_internal_error_maps_to_500(self) -> None:
        assert ERROR_STATUS_MAP[ErrorCode.INTERNAL_ERROR] == HTTPStatus.INTERNAL_SERVER_ERROR

    def test_validation_failed_maps_to_422(self) -> None:
        assert ERROR_STATUS_MAP[ErrorCode.VALIDATION_FAILED] == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_upstream_failures_map_to_502(self) -> None:
        for code in (
            ErrorCode.LLM_UPSTREAM_FAILED,
            ErrorCode.COS_UPSTREAM_FAILED,
            ErrorCode.DB_UPSTREAM_FAILED,
            ErrorCode.WHISPER_UPSTREAM_FAILED,
        ):
            assert ERROR_STATUS_MAP[code] == HTTPStatus.BAD_GATEWAY

    def test_channel_errors_map_to_503(self) -> None:
        assert ERROR_STATUS_MAP[ErrorCode.CHANNEL_QUEUE_FULL] == HTTPStatus.SERVICE_UNAVAILABLE
        assert ERROR_STATUS_MAP[ErrorCode.CHANNEL_DISABLED] == HTTPStatus.SERVICE_UNAVAILABLE

    def test_code_values_match_name(self) -> None:
        """枚举值必须与名字完全一致（大写下划线字符串），便于前端/日志直接引用."""
        for code in ErrorCode:
            assert code.value == code.name


# ── AppException 默认消息回退 ──────────────────────────────────────────────
class TestAppException:
    def test_default_message_fallback_from_map(self) -> None:
        exc = AppException(ErrorCode.TASK_NOT_FOUND)
        assert exc.code is ErrorCode.TASK_NOT_FOUND
        assert exc.message == ERROR_DEFAULT_MESSAGE[ErrorCode.TASK_NOT_FOUND]
        assert exc.details is None

    def test_explicit_message_overrides_default(self) -> None:
        exc = AppException(ErrorCode.TASK_NOT_FOUND, message="自定义消息")
        assert exc.message == "自定义消息"

    def test_details_passthrough(self) -> None:
        payload = {"task_id": "abc-123", "status": "dead"}
        exc = AppException(ErrorCode.TASK_NOT_FOUND, details=payload)
        assert exc.details == payload

    def test_str_representation_includes_code(self) -> None:
        exc = AppException(ErrorCode.COACH_NOT_FOUND)
        assert "COACH_NOT_FOUND" in str(exc)


# ── build_error_response 产出结构 ─────────────────────────────────────────
class TestBuildErrorResponse:
    def test_response_status_matches_map(self) -> None:
        exc = AppException(ErrorCode.TASK_NOT_FOUND)
        resp = build_error_response(exc)
        assert resp.status_code == 404

    def test_response_body_is_error_envelope(self) -> None:
        exc = AppException(ErrorCode.COACH_INACTIVE, details={"coach_id": 7})
        resp = build_error_response(exc)
        body = json.loads(resp.body)
        assert body["success"] is False
        assert body["error"]["code"] == "COACH_INACTIVE"
        assert body["error"]["message"] == ERROR_DEFAULT_MESSAGE[ErrorCode.COACH_INACTIVE]
        assert body["error"]["details"] == {"coach_id": 7}
        assert "data" not in body
        assert "meta" not in body


# ── 处理器集成（最小 app） ────────────────────────────────────────────────
@pytest.fixture
def app_with_handlers() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-app")
    async def raise_app() -> dict[str, str]:
        raise AppException(ErrorCode.JOB_NOT_FOUND, details={"job_id": "j1"})

    @app.get("/raise-unexpected")
    async def raise_unexpected() -> dict[str, str]:
        raise RuntimeError("boom")

    @app.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict[str, int]:
        # item_id 强制整数；GET /items/not-a-number 触发 RequestValidationError
        return {"item_id": item_id}

    return app


class TestHandlersIntegration:
    def test_app_exception_renders_envelope(self, app_with_handlers: FastAPI) -> None:
        client = TestClient(app_with_handlers)
        resp = client.get("/raise-app")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "JOB_NOT_FOUND"
        assert body["error"]["details"] == {"job_id": "j1"}

    def test_unexpected_exception_becomes_internal_error(self, app_with_handlers: FastAPI) -> None:
        client = TestClient(app_with_handlers, raise_server_exceptions=False)
        resp = client.get("/raise-unexpected")
        assert resp.status_code == 500
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INTERNAL_ERROR"
        # 消息不得泄露内部异常细节（RuntimeError 的 "boom" 不应出现）
        assert "boom" not in body["error"]["message"]

    def test_validation_error_becomes_validation_failed(self, app_with_handlers: FastAPI) -> None:
        client = TestClient(app_with_handlers)
        resp = client.get("/items/not-a-number")
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_FAILED"
        # details 至少含 field（可能为 "item_id"）
        assert "details" in body["error"]
