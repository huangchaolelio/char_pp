"""Feature-021 T020 — 7 个新 ErrorCode 的登记完整性单测.

校验：
1. 7 个枚举值都存在于 ``ErrorCode``
2. 7 个值都在 ``ERROR_STATUS_MAP`` 中（HTTP 状态与契约文档一致）
3. 7 个值都在 ``ERROR_DEFAULT_MESSAGE`` 中（消息非空）
4. ``AppException`` 实例化时 ``code`` / ``message`` / ``details`` 三字段透传正确
"""

from __future__ import annotations

from http import HTTPStatus

import pytest

from src.api.errors import ERROR_DEFAULT_MESSAGE, ERROR_STATUS_MAP, AppException, ErrorCode


# 来源：specs/021-video-content-curation/contracts/error-codes.md 表格
# (枚举名, 期望 HTTP 状态)
_FEATURE_021_CODES: list[tuple[str, HTTPStatus]] = [
    ("CURATION_REQUIRED", HTTPStatus.CONFLICT),                  # 409
    ("LOW_QUALITY_SKIP", HTTPStatus.CONFLICT),                    # 409（业务结果，不直接返回客户端）
    ("RUBRIC_INVALID", HTTPStatus.UNPROCESSABLE_ENTITY),          # 422
    ("RUBRIC_VERSION_NOT_FOUND", HTTPStatus.NOT_FOUND),           # 404
    ("CURATION_TIMEOUT", HTTPStatus.INTERNAL_SERVER_ERROR),       # 500
    ("CURATION_LLM_UNAVAILABLE", HTTPStatus.CONFLICT),            # 409（业务结果，不直接返回客户端）
    ("CURATION_RUBRIC_MISMATCH", HTTPStatus.CONFLICT),            # 409
]


@pytest.mark.parametrize("code_name, expected_status", _FEATURE_021_CODES)
def test_error_code_registered_in_status_map(
    code_name: str, expected_status: HTTPStatus
) -> None:
    """7 个错误码都在 ERROR_STATUS_MAP 中，且 HTTP 状态与契约文档一致。"""
    code = ErrorCode[code_name]
    assert code in ERROR_STATUS_MAP, f"{code_name} 未登记到 ERROR_STATUS_MAP"
    assert ERROR_STATUS_MAP[code] == expected_status, (
        f"{code_name} 状态码不一致：登记={ERROR_STATUS_MAP[code]} "
        f"期望={expected_status}（见 contracts/error-codes.md）"
    )


@pytest.mark.parametrize("code_name, _", _FEATURE_021_CODES)
def test_error_code_has_non_empty_default_message(
    code_name: str, _: HTTPStatus
) -> None:
    """7 个错误码都在 ERROR_DEFAULT_MESSAGE 中，且消息非空。"""
    code = ErrorCode[code_name]
    assert code in ERROR_DEFAULT_MESSAGE, f"{code_name} 未登记到 ERROR_DEFAULT_MESSAGE"
    msg = ERROR_DEFAULT_MESSAGE[code]
    assert isinstance(msg, str) and msg.strip(), f"{code_name} 默认消息为空"


def test_curation_required_app_exception_carries_details() -> None:
    """AppException(CURATION_REQUIRED, details={...}) 字段透传正确。"""
    exc = AppException(
        ErrorCode.CURATION_REQUIRED,
        details={"coach_video_classification_id": "abc-123"},
    )
    assert exc.code == ErrorCode.CURATION_REQUIRED
    assert exc.message == ERROR_DEFAULT_MESSAGE[ErrorCode.CURATION_REQUIRED]
    assert exc.details == {"coach_video_classification_id": "abc-123"}


def test_rubric_invalid_app_exception_overrides_message() -> None:
    """AppException(RUBRIC_INVALID, message=...) 自定义消息覆盖默认消息。"""
    exc = AppException(
        ErrorCode.RUBRIC_INVALID,
        message="thresholds.validity_score_accept must be in [0, 1]",
    )
    assert exc.code == ErrorCode.RUBRIC_INVALID
    assert exc.message == "thresholds.validity_score_accept must be in [0, 1]"
    assert exc.details is None


def test_business_result_codes_map_to_409() -> None:
    """LOW_QUALITY_SKIP 与 CURATION_LLM_UNAVAILABLE 是业务结果型，
    存到 extraction_jobs.error_code / segment.rejection_reason 字段，
    不通过 AppException 路径返回给客户端；但章程 IX 仍要求集中登记到
    ERROR_STATUS_MAP，且 CI 守卫 ``test_error_codes_contract.py`` 要求
    HTTP 状态在允许集合（不含 200）内 —— 故映射到 409，与
    KB_CONFLICT_UNRESOLVED 同档位。"""
    assert ERROR_STATUS_MAP[ErrorCode.LOW_QUALITY_SKIP] == HTTPStatus.CONFLICT
    assert ERROR_STATUS_MAP[ErrorCode.CURATION_LLM_UNAVAILABLE] == HTTPStatus.CONFLICT
