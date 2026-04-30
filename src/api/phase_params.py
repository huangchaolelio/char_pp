"""Feature-018 — API 层 BusinessPhase 参数解析辅助.

与 `src/api/enums.py::parse_enum_param` 不同：BusinessPhase 的枚举值为**大写**
（TRAINING / STANDARDIZATION / INFERENCE），不走全局小写归一化；但仍保持
"非法值 ⇒ 400 INVALID_ENUM_VALUE"的合约。
"""

from __future__ import annotations

from src.api.errors import AppException, ErrorCode
from src.models.analysis_task import BusinessPhase


def parse_business_phase(raw: str | None, *, field: str = "business_phase") -> BusinessPhase | None:
    """容错解析 BusinessPhase 查询参数（允许大小写混合，但值域为大写）.

    - None / 空串 ⇒ 返回 None
    - 合法值 ⇒ 返回 BusinessPhase
    - 非法值 ⇒ AppException(INVALID_ENUM_VALUE, 400)
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        return BusinessPhase(s.upper())
    except ValueError as exc:
        raise AppException(
            ErrorCode.INVALID_ENUM_VALUE,
            message=f"Invalid {field} value: {raw!r}",
            details={
                "field": field,
                "value": raw,
                "allowed": [m.value for m in BusinessPhase],
            },
        ) from exc


__all__ = ["parse_business_phase"]
