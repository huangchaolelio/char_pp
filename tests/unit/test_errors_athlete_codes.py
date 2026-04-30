"""Feature-020 — 新 ErrorCode 守卫单测.

目的：
- 确保 ``src/api/errors.py`` 的三张表（``ErrorCode`` 枚举 / ``ERROR_STATUS_MAP`` /
  ``ERROR_DEFAULT_MESSAGE``）同时登记了 Feature-020 的 6 个新错误码；
- 确保 HTTP 状态与默认消息与 ``contracts/error-codes.md`` 完全一致——这是章程
  原则 IX "错误码集中化" 的同步守卫，任何 code 漂移在本单测第一时间 RED。
"""

from __future__ import annotations

from http import HTTPStatus

import pytest

from src.api.errors import ERROR_DEFAULT_MESSAGE, ERROR_STATUS_MAP, ErrorCode


# 对齐 specs/020-athlete-inference-pipeline/contracts/error-codes.md
ATHLETE_ERROR_TABLE: list[tuple[str, HTTPStatus, str]] = [
    ("ATHLETE_ROOT_UNREADABLE", HTTPStatus.BAD_GATEWAY, "运动员视频根路径不可读或凭证无效"),
    ("ATHLETE_DIRECTORY_MAP_MISSING", HTTPStatus.INTERNAL_SERVER_ERROR, "运动员目录映射配置文件缺失"),
    ("ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND", HTTPStatus.NOT_FOUND, "运动员素材记录不存在"),
    ("ATHLETE_VIDEO_NOT_PREPROCESSED", HTTPStatus.CONFLICT, "运动员视频尚未完成预处理，不能直接诊断"),
    ("STANDARD_NOT_AVAILABLE", HTTPStatus.CONFLICT, "该技术类别暂无可用的激活版标准"),
    ("ATHLETE_VIDEO_POSE_UNUSABLE", HTTPStatus.UNPROCESSABLE_ENTITY, "运动员视频姿态提取全程无可用关键点"),
]


@pytest.mark.parametrize(("code_value", "expected_status", "expected_msg"), ATHLETE_ERROR_TABLE)
def test_athlete_error_code_registered(
    code_value: str, expected_status: HTTPStatus, expected_msg: str
) -> None:
    """新 ErrorCode 必须同时出现在三张表中并与 contracts/error-codes.md 一致."""
    # 1. 枚举存在
    assert code_value in ErrorCode.__members__, f"ErrorCode.{code_value} 未定义"
    code = ErrorCode[code_value]

    # 2. HTTP 状态登记正确
    assert code in ERROR_STATUS_MAP, f"{code_value} 缺 ERROR_STATUS_MAP 登记"
    assert ERROR_STATUS_MAP[code] == expected_status, (
        f"{code_value} HTTP 状态漂移：期望 {expected_status.value}，实际 "
        f"{ERROR_STATUS_MAP[code].value}"
    )

    # 3. 默认消息登记正确
    assert code in ERROR_DEFAULT_MESSAGE, f"{code_value} 缺 ERROR_DEFAULT_MESSAGE 登记"
    assert ERROR_DEFAULT_MESSAGE[code] == expected_msg, (
        f"{code_value} 默认消息漂移：期望 {expected_msg!r}，实际 "
        f"{ERROR_DEFAULT_MESSAGE[code]!r}"
    )


def test_all_error_codes_have_status_and_message() -> None:
    """章程原则 IX：所有 ErrorCode 必须在两张表中同时登记，不能漏."""
    missing_status = [c.value for c in ErrorCode if c not in ERROR_STATUS_MAP]
    missing_msg = [c.value for c in ErrorCode if c not in ERROR_DEFAULT_MESSAGE]
    assert not missing_status, f"ErrorCode 缺少 HTTP 状态映射：{missing_status}"
    assert not missing_msg, f"ErrorCode 缺少默认消息映射：{missing_msg}"
