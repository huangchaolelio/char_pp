"""Pydantic schemas for classification API requests and responses.

Feature-023: 移除 tech_category 字段，新增 category_l1/l2/l3/action 四级字段；
PATCH 请求体也对应改为四元组（必须命中 tech_actions 字典 56 行之一）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Request schemas ────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    scan_mode: str = Field(
        ...,
        description="'full'=全量扫描（upsert），'incremental'=仅处理新文件",
        examples=["full", "incremental"],
    )


class ClassificationPatchRequest(BaseModel):
    """Feature-023: PATCH 请求体改为四级字段 + action.

    四元组必须命中 tech_actions 字典；非法值返回 400 ACTION_DICTIONARY_VIOLATION.
    """

    category_l1: str = Field(..., description="握拍方式（如 横拍）")
    category_l2: str = Field(..., description="胶皮类型（如 反胶）")
    category_l3: str = Field(..., description="手部技术·技术大类（如 正手·进攻）")
    action: str = Field(..., description="具体动作名（56 行字典之一）")
    tech_tags: Optional[list[str]] = Field(
        None, description="副技术标签，默认保留原值"
    )


# ── Response schemas ───────────────────────────────────────────────────────────

class ScanStatusResponse(BaseModel):
    task_id: str
    status: str  # pending | running | success | failed
    scanned: Optional[int] = None
    inserted: Optional[int] = None
    updated: Optional[int] = None
    skipped: Optional[int] = None
    errors: Optional[int] = None
    elapsed_s: Optional[float] = None
    error_detail: Optional[str] = None


class ClassificationItem(BaseModel):
    """Feature-023: tech_category → category_l1/l2/l3/action（四级 NULLABLE）."""

    id: UUID
    coach_name: str
    course_series: str
    cos_object_key: str
    filename: str
    category_l1: Optional[str] = None
    category_l2: Optional[str] = None
    category_l3: Optional[str] = None
    action: Optional[str] = None
    tech_tags: list[str]
    raw_tech_desc: Optional[str]
    classification_source: str
    confidence: float
    duration_s: Optional[int]
    kb_extracted: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TechBreakdownItem(BaseModel):
    """Feature-023: tech_category → action 聚合维度."""

    action: str
    count: int
    kb_extracted: int


class CoachSummaryItem(BaseModel):
    coach_name: str
    total_videos: int
    kb_extracted: int
    tech_breakdown: list[TechBreakdownItem]


class ClassificationSummaryResponse(BaseModel):
    coaches: list[CoachSummaryItem]


class ClassificationPatchResponse(BaseModel):
    id: UUID
    category_l1: Optional[str] = None
    category_l2: Optional[str] = None
    category_l3: Optional[str] = None
    action: str
    tech_tags: list[str]
    classification_source: str
    confidence: float
    updated_at: datetime

    model_config = {"from_attributes": True}
