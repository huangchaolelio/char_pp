"""Pydantic schemas for video classification API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Response schemas ─────────────────────────────────────────────────────────

class VideoClassificationResponse(BaseModel):
    cos_object_key: str
    coach_name: str
    tech_category: str
    tech_sub_category: Optional[str] = None
    tech_detail: Optional[str] = None
    video_type: str
    action_type: Optional[str] = None
    classification_confidence: float
    manually_overridden: bool
    override_reason: Optional[str] = None
    classified_at: datetime
    updated_at: datetime


class VideoClassificationListResponse(BaseModel):
    total: int
    items: list[VideoClassificationResponse]


class RefreshResponse(BaseModel):
    refreshed: int = Field(..., description="Number of records inserted/updated")
    skipped: int = Field(..., description="Number of manually_overridden records skipped")
    total_scanned: int


# ── Request schemas ──────────────────────────────────────────────────────────

class VideoClassificationPatch(BaseModel):
    tech_category: Optional[str] = None
    tech_sub_category: Optional[str] = None
    tech_detail: Optional[str] = None
    action_type: Optional[str] = None
    video_type: Optional[str] = None
    override_reason: Optional[str] = Field(
        None, description="Human-readable reason for the override"
    )


class BatchSubmitRequest(BaseModel):
    coach_name: Optional[str] = None
    tech_category: Optional[str] = None
    tech_detail: Optional[str] = None
    action_type: Optional[str] = None
    video_type: Optional[str] = None
    enable_audio_analysis: bool = True
    audio_language: str = "zh"


class BatchSubmitResponse(BaseModel):
    submitted: int
    task_ids: list[str]
