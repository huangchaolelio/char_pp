"""Pydantic schemas for Teaching Tips API (Feature 005).

Endpoints:
  GET  /teaching-tips           → TeachingTipListResponse
  PATCH /teaching-tips/{id}     → TeachingTipResponse
  DELETE /teaching-tips/{id}    → 204 No Content
  POST /tasks/{task_id}/extract-tips → ExtractTipsResponse
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TeachingTipResponse(BaseModel):
    id: UUID
    task_id: UUID
    action_type: str
    tech_phase: str
    tip_text: str
    confidence: float
    source_type: str  # 'auto' | 'human'
    original_text: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # Feature 006: coach info via task JOIN
    coach_id: Optional[UUID] = None
    coach_name: Optional[str] = None

    model_config = {"from_attributes": True}


class TeachingTipListResponse(BaseModel):
    total: int
    items: list[TeachingTipResponse]


class TeachingTipPatch(BaseModel):
    tip_text: Optional[str] = Field(None, description="更新后的建议文字；更新后 source_type 自动变为 'human'")
    tech_phase: Optional[str] = Field(None, description="可选更新技术阶段")


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
