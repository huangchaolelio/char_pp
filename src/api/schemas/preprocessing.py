"""Pydantic v2 schemas — Feature-016 video preprocessing API.

Aligned with:
- specs/016-video-preprocessing-pipeline/contracts/submit_preprocessing.md
- specs/016-video-preprocessing-pipeline/contracts/submit_preprocessing_batch.md
- specs/016-video-preprocessing-pipeline/contracts/get_preprocessing_job.md
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Request: single submission ──────────────────────────────────────────────

class PreprocessingSubmitRequest(BaseModel):
    """Body for ``POST /api/v1/tasks/preprocessing``."""

    model_config = ConfigDict(extra="forbid")

    cos_object_key: str = Field(..., min_length=1, max_length=1024)
    force: bool = Field(False, description="True → supersede any success job + delete its COS objects")
    idempotency_key: Optional[str] = Field(
        None, description="Feature-013 idempotent submission key"
    )


class PreprocessingSubmitItem(BaseModel):
    """Single item of a batch submission."""

    model_config = ConfigDict(extra="forbid")

    cos_object_key: str = Field(..., min_length=1, max_length=1024)
    force: bool = False
    idempotency_key: Optional[str] = None


class PreprocessingBatchSubmitRequest(BaseModel):
    """Body for ``POST /api/v1/tasks/preprocessing/batch``."""

    model_config = ConfigDict(extra="forbid")

    items: list[PreprocessingSubmitItem] = Field(..., min_length=1)


# ── Response: submission ────────────────────────────────────────────────────

class PreprocessingSubmitResponse(BaseModel):
    """Response for ``POST /api/v1/tasks/preprocessing``."""

    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    status: str = Field(..., description="running | success (when reused)")
    reused: bool = Field(..., description="True when force=false hit an existing success job")
    cos_object_key: str
    segment_count: Optional[int] = None
    has_audio: Optional[bool] = None
    started_at: datetime
    completed_at: Optional[datetime] = None


class PreprocessingBatchItemResult(BaseModel):
    """One entry in the ``results`` array of a batch response."""

    model_config = ConfigDict(from_attributes=True)

    cos_object_key: str
    job_id: Optional[UUID] = None
    status: Optional[str] = None
    reused: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class PreprocessingBatchSubmitResponse(BaseModel):
    """Response for ``POST /api/v1/tasks/preprocessing/batch``."""

    model_config = ConfigDict(from_attributes=True)

    submitted: int = Field(..., description="Count of running+success entries (includes reused)")
    reused: int
    failed: int
    results: list[PreprocessingBatchItemResult]


# ── Response: job detail (GET) ──────────────────────────────────────────────

class PreprocessingOriginalMeta(BaseModel):
    """Original-video metadata captured at probe stage."""

    model_config = ConfigDict(from_attributes=True)

    fps: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_ms: Optional[int] = None
    codec: Optional[str] = None
    size_bytes: Optional[int] = None
    has_audio: Optional[bool] = None


class PreprocessingTargetStandard(BaseModel):
    """Standardisation parameters applied during transcode."""

    model_config = ConfigDict(from_attributes=True)

    target_fps: int
    target_short_side: int
    segment_duration_s: int


class PreprocessingAudioView(BaseModel):
    """Preprocessed audio descriptor (None when has_audio=false)."""

    model_config = ConfigDict(from_attributes=True)

    cos_object_key: str
    size_bytes: int


class PreprocessingSegmentView(BaseModel):
    """A single segment row for the GET response."""

    model_config = ConfigDict(from_attributes=True)

    segment_index: int
    start_ms: int
    end_ms: int
    cos_object_key: str
    size_bytes: int


class PreprocessingJobResponse(BaseModel):
    """Response for ``GET /api/v1/video-preprocessing/{job_id}``."""

    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    cos_object_key: str
    status: str
    force: bool
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    segment_count: Optional[int] = None
    has_audio: bool
    error_message: Optional[str] = None
    original_meta: Optional[PreprocessingOriginalMeta] = None
    target_standard: Optional[PreprocessingTargetStandard] = None
    audio: Optional[PreprocessingAudioView] = None
    segments: list[PreprocessingSegmentView] = Field(default_factory=list)


# ── Response: job list (GET /video-preprocessing) ───────────────────────────

class PreprocessingJobListItem(BaseModel):
    """Summary row for ``GET /api/v1/video-preprocessing`` list endpoint.

    Intentionally omits ``segments`` / ``original_meta`` / ``target_standard``
    / ``audio`` to keep list payloads small — callers drill into
    ``GET /api/v1/video-preprocessing/{job_id}`` for full detail.
    """

    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    cos_object_key: str
    status: str = Field(..., description="running | success | failed | superseded")
    force: bool
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    segment_count: Optional[int] = None
    has_audio: bool
    error_message: Optional[str] = None
    original_meta: Optional[PreprocessingOriginalMeta] = None
    target_standard: Optional[PreprocessingTargetStandard] = None
    audio: Optional[PreprocessingAudioView] = None
    segments: list[PreprocessingSegmentView] = Field(default_factory=list)
