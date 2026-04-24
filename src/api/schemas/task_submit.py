"""Shared request/response envelope for Feature 013 task submission endpoints.

Per-task-type **request** schemas now live in dedicated modules (US3/T042,
FR-002 decoupling):
  * :mod:`src.api.schemas.classification_task`
  * :mod:`src.api.schemas.kb_extraction_task`
  * :mod:`src.api.schemas.diagnosis_task`

This module keeps only the shared **response** envelope plus admin schemas,
and re-exports the per-type request models so existing imports continue to
work unchanged (no rewrite of the router or contract tests required).

Aligned with:
  * ``specs/013-task-pipeline-redesign/contracts/task_submit.yaml``
  * ``specs/013-task-pipeline-redesign/contracts/channel_status.yaml``

Rejection codes: ``QUEUE_FULL`` / ``DUPLICATE_TASK`` / ``CLASSIFICATION_REQUIRED`` /
``CHANNEL_DISABLED`` / ``BATCH_TOO_LARGE`` / ``INVALID_INPUT``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Re-export per-type request schemas from their dedicated modules so legacy
# callers (router, tests) keep working with ``from src.api.schemas.task_submit
# import ClassificationSingleRequest`` etc.
from src.api.schemas.classification_task import (  # noqa: F401
    ClassificationBatchRequest,
    ClassificationSingleRequest,
)
from src.api.schemas.diagnosis_task import (  # noqa: F401
    DiagnosisBatchRequest,
    DiagnosisSingleRequest,
)
from src.api.schemas.kb_extraction_task import (  # noqa: F401
    KbExtractionBatchRequest,
    KbExtractionSingleRequest,
)


# ──────────────────────────────────────────────────────────────────────────────
# Shared response envelope
# ──────────────────────────────────────────────────────────────────────────────


RejectionCode = Literal[
    "QUEUE_FULL",
    "DUPLICATE_TASK",
    "CLASSIFICATION_REQUIRED",
    "CHANNEL_DISABLED",
    "INVALID_INPUT",
]


class ChannelSnapshot(BaseModel):
    """Single channel's live capacity snapshot."""

    model_config = ConfigDict(from_attributes=True)

    task_type: str
    queue_capacity: int
    concurrency: int
    current_pending: int
    current_processing: int
    remaining_slots: int
    enabled: bool
    recent_completion_rate_per_min: float = 0.0


class SubmissionItem(BaseModel):
    """Per-item outcome in a (possibly-single) submission."""

    model_config = ConfigDict(from_attributes=True)

    index: int = Field(..., description="Position in the request `items` array (0-based).")
    accepted: bool
    task_id: Optional[UUID] = None
    cos_object_key: Optional[str] = None
    rejection_code: Optional[RejectionCode] = None
    rejection_message: Optional[str] = None
    existing_task_id: Optional[UUID] = Field(
        None,
        description="Set only when rejection_code=DUPLICATE_TASK — points at the live task.",
    )


class SubmissionResult(BaseModel):
    """Response body for both single and batch submission endpoints."""

    model_config = ConfigDict(from_attributes=True)

    task_type: str
    accepted: int
    rejected: int
    items: list[SubmissionItem]
    channel: ChannelSnapshot
    submitted_at: datetime


# ──────────────────────────────────────────────────────────────────────────────
# Admin channel patch
# ──────────────────────────────────────────────────────────────────────────────


class ChannelConfigPatch(BaseModel):
    """``PATCH /api/v1/admin/channels/{task_type}``."""

    model_config = ConfigDict(extra="forbid")

    queue_capacity: Optional[int] = Field(None, gt=0, le=10000)
    concurrency: Optional[int] = Field(None, gt=0, le=64)
    enabled: Optional[bool] = None


class DataResetRequest(BaseModel):
    """``POST /api/v1/admin/reset-task-pipeline``."""

    model_config = ConfigDict(extra="forbid")

    confirmation_token: str = Field(..., min_length=1)
    dry_run: bool = False


class ResetReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reset_at: datetime
    dry_run: bool
    deleted_counts: dict[str, int]
    preserved_counts: dict[str, int]
    duration_ms: int
