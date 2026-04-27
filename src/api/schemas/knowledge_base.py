"""Pydantic schemas for knowledge-base API requests and responses.

Aligned with contracts/api.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── List versions ────────────────────────────────────────────────────────────

class KnowledgeBaseVersionItem(BaseModel):
    version: str
    status: str
    action_types_covered: list[str]
    point_count: int
    approved_at: Optional[datetime] = None


# ── Version detail ─────────────────────────────────────────────────────────��
class TechPointDetail(BaseModel):
    action_type: str
    dimension: str
    param_min: float
    param_max: float
    param_ideal: float
    unit: str
    extraction_confidence: float


class KnowledgeBaseDetailResponse(BaseModel):
    version: str
    status: str
    action_types_covered: list[str]
    point_count: int
    tech_points: list[TechPointDetail]
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    notes: Optional[str] = None


# ── Approve request / response ───────────────────────────────────────────────

class ApproveRequest(BaseModel):
    approved_by: str = Field(..., description="审核通过的专家姓名")
    notes: Optional[str] = Field(None, description="审核说明")


class ApproveResponse(BaseModel):
    version: str
    status: str
    approved_by: str
    approved_at: datetime
    previous_active_version: Optional[str] = None
