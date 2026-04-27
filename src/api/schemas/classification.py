"""Pydantic schemas for classification API requests and responses.

Aligned with contracts/api.md (Feature 008).
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
    tech_category: str = Field(..., description="新的主技术类别 ID（需为有效枚举值）")
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
    id: UUID
    coach_name: str
    course_series: str
    cos_object_key: str
    filename: str
    tech_category: str
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
    tech_category: str
    label: str
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
    tech_category: str
    tech_tags: list[str]
    classification_source: str
    confidence: float
    updated_at: datetime

    model_config = {"from_attributes": True}
