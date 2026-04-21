"""Pydantic schemas for Coach API and Calibration API (Feature 006)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Coach CRUD schemas ────────────────────────────────────────────────────────

class CoachCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="教练姓名（全局唯一）")
    bio: Optional[str] = Field(None, description="教练简介（可选）")


class CoachUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="新姓名")
    bio: Optional[str] = Field(None, description="新简介")


class CoachResponse(BaseModel):
    id: UUID
    name: str
    bio: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Task-Coach association schemas ────────────────────────────────────────────

class TaskCoachUpdate(BaseModel):
    coach_id: Optional[UUID] = Field(None, description="教练 ID；传 null 解除关联")


class TaskCoachResponse(BaseModel):
    task_id: UUID
    coach_id: Optional[UUID] = None
    coach_name: Optional[str] = None


# ── Calibration view schemas ──────────────────────────────────────────────────

class CoachTechPointEntry(BaseModel):
    coach_id: UUID
    coach_name: str
    param_min: float
    param_ideal: float
    param_max: float
    unit: str
    extraction_confidence: float
    source_count: int = Field(..., description="该教练在此维度的记录数")


class TechPointCalibrationView(BaseModel):
    action_type: str
    dimension: str
    coaches: list[CoachTechPointEntry]


class CoachTipGroup(BaseModel):
    coach_id: UUID
    coach_name: str
    tips: list[str]


class TeachingTipCalibrationView(BaseModel):
    action_type: str
    tech_phase: str
    coaches: list[CoachTipGroup]
