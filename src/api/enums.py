"""API 层枚举值统一归一化 + 校验辅助（Feature-017 阶段 5 T056）.

章程 v1.4.0 原则 IX 规则：

    "枚举型查询参数（如 tech_category、status、task_type）在服务端统一按小写
     下划线归一化；非法值返回 400 + INVALID_ENUM_VALUE，details 含合法取值列表"

该模块提供 ``normalize_enum_value`` 与 ``parse_enum_param`` 两个函数，
供路由层集中调用，避免 N 处路由各自实现 ``.lower().replace(...)``。
"""

from __future__ import annotations

from typing import Iterable, TypeVar

from src.api.errors import AppException, ErrorCode

EnumValueT = TypeVar("EnumValueT")


def normalize_enum_value(raw: str) -> str:
    """统一归一化枚举字符串：去首尾空白 + 全小写 + 中划线转下划线.

    例：``"  Video-Classification  "`` → ``"video_classification"``。
    """
    if raw is None:
        return raw  # type: ignore[return-value]
    return raw.strip().lower().replace("-", "_")


def parse_enum_param(
    value: str,
    *,
    field: str,
    enum_cls: type,
) -> EnumValueT:
    """归一化 + 解析 str 枚举值，失败抛统一 AppException.

    - ``enum_cls`` 必须是以 ``str`` 为基础的枚举（``class Foo(str, Enum)``）。
    - 失败时抛 ``AppException(INVALID_ENUM_VALUE)``，``details`` 包含
      ``field`` / ``value``（原值）/ ``allowed`` 合法列表。

    示例::

        status_enum = parse_enum_param(status, field="status", enum_cls=TaskStatus)
    """
    normalized = normalize_enum_value(value)
    try:
        return enum_cls(normalized)  # type: ignore[return-value,call-arg]
    except ValueError as exc:
        allowed = [m.value for m in enum_cls]  # type: ignore[attr-defined]
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            message=f"Invalid {field} value: {value!r}",
            details={"field": field, "value": value, "allowed": allowed},
        ) from exc


def validate_enum_choice(
    value: str,
    *,
    field: str,
    allowed: Iterable[str],
) -> str:
    """归一化 + 枚举白名单校验（不绑定到 Enum 类的情况）.

    返回归一化后的值；失败抛 ``AppException(INVALID_ENUM_VALUE)``。
    """
    normalized = normalize_enum_value(value)
    allowed_set = {normalize_enum_value(a) for a in allowed}
    if normalized not in allowed_set:
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            message=f"Invalid {field} value: {value!r}",
            details={"field": field, "value": value, "allowed": sorted(allowed_set)},
        )
    return normalized
