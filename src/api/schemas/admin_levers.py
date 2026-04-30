"""Feature-018 — `GET /api/v1/admin/levers` 响应 DTO.

对齐 specs/018-workflow-standardization/data-model.md § 8
与 contracts/admin-levers.yaml。

敏感键（sensitive=true）响应中：
- current_value = None（路由层显式抹平）
- last_changed_at = None / last_changed_by = None
- is_configured = True|False

非敏感键：
- current_value 有值
- is_configured = None（Pydantic 输出时省略）
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.api.schemas.business_workflow import BusinessPhaseLiteral


LeverType = Literal["runtime_params", "algorithm_models", "rules_prompts"]
LeverSource = Literal["db_table", "env", "config_file"]
LeverRestartScope = Literal["none", "worker", "api"]


class LeverEntry(BaseModel):
    """单个优化杠杆条目."""

    model_config = ConfigDict(extra="forbid")

    key: str
    type: LeverType
    source: LeverSource
    effective_in_seconds: int | None = None
    restart_scope: LeverRestartScope
    business_phase: list[BusinessPhaseLiteral]

    # 非敏感键路径
    current_value: str | int | bool | None = None
    last_changed_at: datetime | None = None
    last_changed_by: str | None = None

    # 敏感键路径：sensitive=true 的条目仅置此字段，current_value 恒 None
    is_configured: bool | None = None


class LeverGroups(BaseModel):
    """三类分组的优化杠杆台账."""

    model_config = ConfigDict(extra="forbid")

    runtime_params: list[LeverEntry]
    algorithm_models: list[LeverEntry]
    rules_prompts: list[LeverEntry]


__all__ = [
    "LeverType",
    "LeverSource",
    "LeverRestartScope",
    "LeverEntry",
    "LeverGroups",
]
