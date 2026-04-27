"""Feature-017 阶段 6 T065：ErrorCode 枚举 ↔ ERROR_STATUS_MAP ↔ ERROR_DEFAULT_MESSAGE 三元覆盖测试.

目标：防止 ErrorCode 新增/改名时，ERROR_STATUS_MAP 或 ERROR_DEFAULT_MESSAGE
漏配导致运行时 KeyError / 默认状态错误。

覆盖方式：对 ErrorCode 的每个枚举值逐一参数化，
  1. 断言其出现在 ERROR_STATUS_MAP（可查到 HTTP 状态）
  2. 断言其出现在 ERROR_DEFAULT_MESSAGE（可查到默认消息）
  3. 断言其 HTTP 状态属于合法范围 {400, 401, 404, 409, 410, 422, 500, 502, 503}
  4. 断言 AppException 可无参构造（message 走默认映射）

此外验证 ErrorEnvelope 的 code 字段序列化后保持原始字符串形态（非枚举对象）。
"""

from __future__ import annotations

from http import HTTPStatus

import pytest

from src.api.errors import (
    AppException,
    ERROR_DEFAULT_MESSAGE,
    ERROR_STATUS_MAP,
    ErrorCode,
    build_error_response,
)

_ALLOWED_STATUSES = {
    HTTPStatus.BAD_REQUEST,          # 400
    HTTPStatus.UNAUTHORIZED,         # 401
    HTTPStatus.NOT_FOUND,            # 404
    HTTPStatus.CONFLICT,             # 409
    HTTPStatus.GONE,                 # 410
    HTTPStatus.UNPROCESSABLE_ENTITY,  # 422
    HTTPStatus.INTERNAL_SERVER_ERROR,  # 500
    HTTPStatus.BAD_GATEWAY,          # 502
    HTTPStatus.SERVICE_UNAVAILABLE,  # 503
}


@pytest.mark.parametrize("code", list(ErrorCode), ids=lambda c: c.value)
class TestErrorCodeCoverage:
    """参数化覆盖 ErrorCode 全部 39 个枚举值."""

    def test_has_status_mapping(self, code: ErrorCode) -> None:
        assert code in ERROR_STATUS_MAP, f"{code.value} 缺少 ERROR_STATUS_MAP 映射"
        status = ERROR_STATUS_MAP[code]
        assert status in _ALLOWED_STATUSES, (
            f"{code.value} 的 HTTP 状态 {status} 不在允许集合 {sorted(s.value for s in _ALLOWED_STATUSES)}"
        )

    def test_has_default_message(self, code: ErrorCode) -> None:
        assert code in ERROR_DEFAULT_MESSAGE, f"{code.value} 缺少 ERROR_DEFAULT_MESSAGE 映射"
        msg = ERROR_DEFAULT_MESSAGE[code]
        assert isinstance(msg, str) and msg, f"{code.value} 的默认消息为空"

    def test_app_exception_bare_construction(self, code: ErrorCode) -> None:
        """AppException(code) 可无参构造并渲染为合法 ErrorEnvelope."""
        exc = AppException(code)
        response = build_error_response(exc)
        # JSONResponse 的状态码应与 ERROR_STATUS_MAP 一致
        assert response.status_code == ERROR_STATUS_MAP[code]


# ── Non-parametrized 全局断言 ─────────────────────────────────────────────────

class TestErrorCodeInvariants:
    def test_code_count_stable(self) -> None:
        """ErrorCode 总数与 ERROR_STATUS_MAP / ERROR_DEFAULT_MESSAGE 严格一致."""
        total = len(ErrorCode)
        assert len(ERROR_STATUS_MAP) == total, (
            f"ERROR_STATUS_MAP 条目数 ({len(ERROR_STATUS_MAP)}) ≠ ErrorCode 枚举数 ({total})"
        )
        assert len(ERROR_DEFAULT_MESSAGE) == total, (
            f"ERROR_DEFAULT_MESSAGE 条目数 ({len(ERROR_DEFAULT_MESSAGE)}) ≠ ErrorCode 枚举数 ({total})"
        )

    def test_serialized_code_is_string(self) -> None:
        """ErrorEnvelope 序列化后 code 字段必须是纯字符串（非枚举对象）."""
        exc = AppException(ErrorCode.TASK_NOT_FOUND, details={"task_id": "abc"})
        response = build_error_response(exc)
        # JSONResponse.body 是 bytes，解 JSON 再看 code 类型
        import json
        body = json.loads(response.body)
        assert body["success"] is False
        assert body["error"]["code"] == "TASK_NOT_FOUND"
        assert isinstance(body["error"]["code"], str)
        assert body["error"]["details"] == {"task_id": "abc"}
