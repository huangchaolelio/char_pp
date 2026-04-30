"""Feature-019 — KB per-category lifecycle API schemas (Pydantic v2).

对齐 contracts/{kb-versions-list,kb-version-detail,kb-version-approve}.yaml 与
data-model.md § 实体 1。

KB 响应体统一以 `(tech_category, version)` 复合主键身份，`version` 为 per-category
独立递增整数。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ── List / Detail 公用项 ─────────────────────────────────────────────────

class KbVersionItem(BaseModel):
    """KB 列表项 — 对齐 contracts/kb-versions-list.yaml::KbVersionItem"""

    model_config = ConfigDict(from_attributes=True)

    tech_category: str
    version: int = Field(..., ge=1)
    status: Literal["draft", "active", "archived"]
    point_count: int = Field(..., ge=0)
    extraction_job_id: str   # UUID 序列化为字符串（前端易处理）
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    notes: str | None = None


class DimensionsSummary(BaseModel):
    """详情接口的 expert_tech_points 摘要。"""

    model_config = ConfigDict(from_attributes=True)

    total_points: int = Field(..., ge=0)
    dimensions: list[str]
    conflict_count: int = Field(..., ge=0)


class KbVersionDetail(KbVersionItem):
    """KB 详情 — 列表项 + dimensions_summary。"""

    dimensions_summary: DimensionsSummary


# ── Approve 请求 / 响应 ─────────────────────────────────────────────────

class ApproveKbRequest(BaseModel):
    """POST /knowledge-base/versions/{tc}/{ver}/approve 请求体。"""

    model_config = ConfigDict(extra="forbid")

    approved_by: str = Field(..., min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class TipsUpdatedStats(BaseModel):
    """approve 事务联动 teaching_tips 的批量统计。"""

    archived_count: int = Field(..., ge=0)
    activated_count: int = Field(..., ge=0)


class ApproveKbResponse(BaseModel):
    """approve 成功响应数据（外层仍包 SuccessEnvelope[T]）。"""

    model_config = ConfigDict(from_attributes=True)

    new_active: KbVersionItem
    previous_active_version: int | None = None
    tips_updated: TipsUpdatedStats
