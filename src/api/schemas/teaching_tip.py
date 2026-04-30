"""Pydantic schemas for Teaching Tips API (Feature 005 / Feature-019 adapted).

Feature-019 变更:
  - TeachingTipResponse 删除 `action_type`（ORM 已删列）；新增 `tech_category` / `kb_tech_category` / `kb_version` / `status`
  - TeachingTipCreateRequest（新）：人工创建 tip 必填 `tech_category` + `kb_tech_category` + `kb_version`
  - TeachingTipPatch 不涉及生命周期字段，签名保持兼容

Endpoints (Feature-017 aligned: list 采用 SuccessEnvelope[list[TeachingTipResponse]]
+ PaginationMeta 来包装，包装类 TeachingTipListResponse 已下线):
  GET  /teaching-tips           → SuccessEnvelope[list[TeachingTipResponse]]
  PATCH /teaching-tips/{id}     → SuccessEnvelope[TeachingTipResponse]
  DELETE /teaching-tips/{id}    → 204 No Content
  POST /tasks/{task_id}/extract-tips → SuccessEnvelope[ExtractTipsResponse]
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TeachingTipResponse(BaseModel):
    id: UUID
    task_id: Optional[UUID] = None  # Feature-019: tips 生命周期与 task 解耦（nullable）
    # Feature-019: 删除 action_type；新增 tech_category + kb_* + status
    tech_category: str
    kb_tech_category: str
    kb_version: int
    status: str  # 'draft' | 'active' | 'archived'
    tech_phase: str
    tip_text: str
    confidence: float
    source_type: str  # 'auto' | 'human'
    original_text: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # Feature 006: coach info via task JOIN（任务未 cascade 时均为 None）
    coach_id: Optional[UUID] = None
    coach_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TeachingTipPatch(BaseModel):
    """PATCH /teaching-tips/{id} — 人工编辑（仅改文本/阶段，不改生命周期字段）."""

    tip_text: Optional[str] = Field(
        None, description="更新后的建议文字；更新后 source_type 自动变为 'human'"
    )
    tech_phase: Optional[str] = Field(None, description="可选更新技术阶段")

    model_config = ConfigDict(extra="forbid")


class TeachingTipCreateRequest(BaseModel):
    """Feature-019 T035 新增：人工直接创建 tip 的请求体（暂预留给后续 POST /teaching-tips）。

    FR-024 人工标注独立通道：``source_type`` 强制为 'human'，生命周期字段全必填。
    """

    tech_category: str = Field(..., min_length=1, max_length=64)
    kb_tech_category: str = Field(..., min_length=1, max_length=64)
    kb_version: int = Field(..., ge=1)
    tech_phase: str = Field(..., min_length=1, max_length=30)
    tip_text: str = Field(..., min_length=1, max_length=2000)
    confidence: float = Field(1.0, ge=0.0, le=1.0)  # 人工默认 1.0
    task_id: Optional[UUID] = Field(
        None,
        description="可选关联任务；不传则创建独立 tip（生命周期与 task 解耦，Feature-019）",
    )

    model_config = ConfigDict(extra="forbid")


class ExtractTipsResponse(BaseModel):
    task_id: UUID
    status: str = "extracting"
    message: str = "教学建议提炼已触发，将在30秒内完成"
    preserved_human_count: int = 0


# ── Embedded in CoachingAdvice response (US2) ─────────────────────────────────


class TeachingTipRef(BaseModel):
    """Minimal tip reference embedded in CoachingAdviceItem."""

    tip_text: str
    tech_phase: str
    source_type: str  # 'auto' | 'human'
