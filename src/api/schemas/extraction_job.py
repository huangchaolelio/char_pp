"""Pydantic schemas — Feature 014 extraction-jobs API.

Aligned with specs/014-kb-extraction-pipeline/contracts/extraction_jobs.yaml.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Subtask / Step ──────────────────────────────────────────────────────────

class PipelineStepResponse(BaseModel):
    """A single step in the extraction DAG."""

    model_config = ConfigDict(from_attributes=True)

    step_type: str = Field(..., description="One of 6 StepType values")
    status: str = Field(..., description="pending | running | success | failed | skipped")
    retry_count: int = Field(0)
    error_message: Optional[str] = None
    output_summary: Optional[dict] = None
    output_artifact_path: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    depends_on: list[str] = Field(
        default_factory=list,
        description="Upstream step types that must complete before this step runs",
    )


# ── Job progress ────────────────────────────────────────────────────────────

class ProgressResponse(BaseModel):
    total_steps: int
    success_steps: int
    failed_steps: int
    skipped_steps: int
    running_steps: int
    pending_steps: int
    percent: float = Field(..., description="Completion ratio, 0.0 .. 1.0")


# ── Job summary (list) ──────────────────────────────────────────────────────

class ExtractionJobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    analysis_task_id: UUID
    cos_object_key: str
    tech_category: str
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    conflict_count: int = 0
    error_message: Optional[str] = None


# ── Job detail (single) ─────────────────────────────────────────────────────

class ExtractionJobDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    analysis_task_id: UUID
    cos_object_key: str
    tech_category: str
    status: str
    worker_hostname: Optional[str] = None
    enable_audio_analysis: bool
    audio_language: str
    force: bool
    superseded_by_job_id: Optional[UUID] = None
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    intermediate_cleanup_at: Optional[datetime] = None
    steps: list[PipelineStepResponse]
    progress: ProgressResponse
    conflict_count: int = 0


# ── Pagination envelope ─────────────────────────────────────────────────────

class ExtractionJobListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ExtractionJobSummary]


# ── Rerun ───────────────────────────────────────────────────────────────────

class RerunRequest(BaseModel):
    force_from_scratch: bool = Field(
        False,
        description="When true, reset ALL steps (including success) to pending; "
                    "required if intermediate artifacts have been cleaned up.",
    )


class RerunResponse(BaseModel):
    job_id: UUID
    status: str
    reset_steps: list[str]


# ── Error envelope (aligned with Feature-013) ───────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail
