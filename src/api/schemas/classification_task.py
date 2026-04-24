"""Classification task schemas (Feature 013 US3 — FR-002 decoupling).

Isolated from ``kb_extraction_task`` and ``diagnosis_task`` so a request model
for one pipeline cannot leak fields that belong to another. Import from here
when adding classification-only routes; import from ``task_submit`` only for
the shared response envelope (``SubmissionResult``, ``ChannelSnapshot``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ClassificationSingleRequest(BaseModel):
    """``POST /api/v1/tasks/classification`` — single coach video."""

    model_config = ConfigDict(extra="forbid")

    cos_object_key: str = Field(..., min_length=1, max_length=1000)
    force: bool = Field(
        False,
        description=(
            "When true, bypass the `completed` idempotency guard "
            "(re-run a previously-successful classification)."
        ),
    )


class ClassificationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ClassificationSingleRequest] = Field(..., min_length=1)
