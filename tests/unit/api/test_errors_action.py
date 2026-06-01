"""Feature-023 — Unit tests for action-related error codes.

T006 (RED → GREEN once T005 lands):
- ACTION_NOT_FOUND maps to 404
- ACTION_DICTIONARY_VIOLATION maps to 400
- STANDARD_NOT_AVAILABLE_FOR_ACTION maps to 503
- NO_ACTIVE_KB_FOR_ACTION maps to 409 (renamed from NO_ACTIVE_KB_FOR_CATEGORY)

Each test asserts both ERROR_STATUS_MAP and ERROR_DEFAULT_MESSAGE entries
exist (single-source-of-truth invariant from constitution principle IX).
"""

from __future__ import annotations

from http import HTTPStatus

import pytest

from src.api.errors import (
    ERROR_DEFAULT_MESSAGE,
    ERROR_STATUS_MAP,
    AppException,
    ErrorCode,
)


def test_action_not_found_maps_to_404() -> None:
    assert ErrorCode.ACTION_NOT_FOUND in ERROR_STATUS_MAP
    assert ERROR_STATUS_MAP[ErrorCode.ACTION_NOT_FOUND] == HTTPStatus.NOT_FOUND
    assert ErrorCode.ACTION_NOT_FOUND in ERROR_DEFAULT_MESSAGE
    msg = ERROR_DEFAULT_MESSAGE[ErrorCode.ACTION_NOT_FOUND]
    assert msg and isinstance(msg, str)

    exc = AppException(ErrorCode.ACTION_NOT_FOUND, details={"action": "未知动作"})
    assert exc.code is ErrorCode.ACTION_NOT_FOUND
    assert exc.message == msg
    assert exc.details == {"action": "未知动作"}


def test_action_dictionary_violation_maps_to_400() -> None:
    assert ErrorCode.ACTION_DICTIONARY_VIOLATION in ERROR_STATUS_MAP
    assert (
        ERROR_STATUS_MAP[ErrorCode.ACTION_DICTIONARY_VIOLATION]
        == HTTPStatus.BAD_REQUEST
    )
    assert ErrorCode.ACTION_DICTIONARY_VIOLATION in ERROR_DEFAULT_MESSAGE
    msg = ERROR_DEFAULT_MESSAGE[ErrorCode.ACTION_DICTIONARY_VIOLATION]
    assert msg and isinstance(msg, str)


def test_standard_not_available_for_action_maps_to_503() -> None:
    assert ErrorCode.STANDARD_NOT_AVAILABLE_FOR_ACTION in ERROR_STATUS_MAP
    assert (
        ERROR_STATUS_MAP[ErrorCode.STANDARD_NOT_AVAILABLE_FOR_ACTION]
        == HTTPStatus.SERVICE_UNAVAILABLE
    )
    assert ErrorCode.STANDARD_NOT_AVAILABLE_FOR_ACTION in ERROR_DEFAULT_MESSAGE


def test_no_active_kb_for_action_maps_to_409() -> None:
    assert ErrorCode.NO_ACTIVE_KB_FOR_ACTION in ERROR_STATUS_MAP
    assert (
        ERROR_STATUS_MAP[ErrorCode.NO_ACTIVE_KB_FOR_ACTION]
        == HTTPStatus.CONFLICT
    )
    assert ErrorCode.NO_ACTIVE_KB_FOR_ACTION in ERROR_DEFAULT_MESSAGE


def test_legacy_codes_physically_removed() -> None:
    """Feature-023: STANDARD_NOT_AVAILABLE / NO_ACTIVE_KB_FOR_CATEGORY 已物理删除（章程 v2.0.0）."""
    enum_values = {c.value for c in ErrorCode}
    assert "STANDARD_NOT_AVAILABLE" not in enum_values
    assert "NO_ACTIVE_KB_FOR_CATEGORY" not in enum_values


def test_all_error_codes_in_status_map_and_message_map() -> None:
    """单一事实来源：所有 ErrorCode 必须同时登记 status_map 和 message_map."""
    for code in ErrorCode:
        assert code in ERROR_STATUS_MAP, f"{code.value} missing in ERROR_STATUS_MAP"
        assert (
            code in ERROR_DEFAULT_MESSAGE
        ), f"{code.value} missing in ERROR_DEFAULT_MESSAGE"
