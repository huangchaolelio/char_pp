"""Feature-017 — 契约测试共享夹具与断言辅助函数.

由 `tests/contract/**/*.py` 统一复用，确保每个业务合约测试断言
`contracts/response-envelope.schema.json` 中约定的信封结构。
"""

from __future__ import annotations

from typing import Any


def assert_success_envelope(
    body: dict[str, Any],
    *,
    expect_meta: bool = False,
) -> Any:
    """断言响应体是合格的成功信封，返回内层 ``data`` 便于后续字段级断言.

    Args:
        body: 响应体（已 json 反序列化的 dict）
        expect_meta: 若为 True，额外断言 ``meta`` 为非 None 的分页元信息

    Returns:
        ``body["data"]`` 值，供调用方做进一步字段级断言

    断言覆盖：
    - 顶层必须有 ``success == True``
    - 顶层必须有 ``data`` 字段（可为 None / dict / list）
    - 顶层不得出现 ``error`` 字段
    - 若 expect_meta=True，则 ``meta`` 必须为含 ``page``、``page_size``、``total`` 的 dict
    """
    assert isinstance(body, dict), f"response body must be dict, got {type(body).__name__}"
    assert body.get("success") is True, (
        f"expected success=True in envelope, got body={body!r}"
    )
    assert "data" in body, f"success envelope must contain 'data', got keys={list(body.keys())}"
    assert "error" not in body, "success envelope must not contain 'error'"
    if expect_meta:
        meta = body.get("meta")
        assert isinstance(meta, dict), f"expected meta dict, got {meta!r}"
        for key in ("page", "page_size", "total"):
            assert key in meta, f"meta missing '{key}': meta={meta!r}"
            assert isinstance(meta[key], int), f"meta.{key} must be int, got {type(meta[key]).__name__}"
    return body["data"]


def assert_error_envelope(
    body: dict[str, Any],
    *,
    code: str | None = None,
) -> dict[str, Any]:
    """断言响应体是合格的错误信封，返回内层 ``error`` 字典便于 details 断言.

    Args:
        body: 响应体（已 json 反序列化的 dict）
        code: 若提供，额外断言 ``error.code == code``

    Returns:
        ``body["error"]`` 字典，供调用方做进一步 ``details`` 断言

    断言覆盖：
    - 顶层必须有 ``success == False``
    - 顶层必须有 ``error`` 字段，内含 ``code`` + ``message``
    - 顶层不得出现 ``data`` 与 ``meta`` 字段
    - 若指定了 ``code``，则 ``body["error"]["code"]`` 必须与之一致
    """
    assert isinstance(body, dict), f"response body must be dict, got {type(body).__name__}"
    assert body.get("success") is False, (
        f"expected success=False in envelope, got body={body!r}"
    )
    assert "error" in body, f"error envelope must contain 'error', got keys={list(body.keys())}"
    assert "data" not in body, "error envelope must not contain 'data'"
    assert "meta" not in body, "error envelope must not contain 'meta'"
    err = body["error"]
    assert isinstance(err, dict), f"error must be dict, got {type(err).__name__}"
    assert "code" in err and isinstance(err["code"], str)
    assert "message" in err and isinstance(err["message"], str)
    if code is not None:
        assert err["code"] == code, f"expected error.code={code!r}, got {err['code']!r}"
    return err


__all__ = ["assert_success_envelope", "assert_error_envelope"]
