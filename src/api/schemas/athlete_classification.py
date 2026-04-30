"""Feature-020 — 运动员推理流水线 Pydantic Schemas.

严格对齐 ``specs/020-athlete-inference-pipeline/data-model.md`` § 6 与各
``contracts/*.md``。所有 Request 强制 ``extra="forbid"``（章程原则 IX 附加约束）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ══════════════════════════════════════════════════════════════════════════
# Request Schemas
# ══════════════════════════════════════════════════════════════════════════


class AthleteScanRequest(BaseModel):
    """POST /api/v1/athlete-classifications/scan 请求体."""

    model_config = ConfigDict(extra="forbid")

    scan_mode: str = Field("full", pattern="^(full|incremental)$")


class AthletePreprocessingSubmitRequest(BaseModel):
    """POST /api/v1/tasks/athlete-preprocessing 单条请求体."""

    model_config = ConfigDict(extra="forbid")

    athlete_video_classification_id: UUID
    force: bool = False


class AthletePreprocessingBatchItem(BaseModel):
    """批量预处理请求体的单个 item."""

    model_config = ConfigDict(extra="forbid")

    athlete_video_classification_id: UUID
    force: bool = False


class AthletePreprocessingBatchRequest(BaseModel):
    """POST /api/v1/tasks/athlete-preprocessing/batch 批量请求体."""

    model_config = ConfigDict(extra="forbid")

    items: list[AthletePreprocessingBatchItem] = Field(..., min_length=1, max_length=50)


class AthleteDiagnosisSubmitRequest(BaseModel):
    """POST /api/v1/tasks/athlete-diagnosis 单条请求体."""

    model_config = ConfigDict(extra="forbid")

    athlete_video_classification_id: UUID
    force: bool = False


class AthleteDiagnosisBatchItem(BaseModel):
    """批量诊断请求体的单个 item."""

    model_config = ConfigDict(extra="forbid")

    athlete_video_classification_id: UUID
    force: bool = False


class AthleteDiagnosisBatchRequest(BaseModel):
    """POST /api/v1/tasks/athlete-diagnosis/batch 批量请求体."""

    model_config = ConfigDict(extra="forbid")

    items: list[AthleteDiagnosisBatchItem] = Field(..., min_length=1, max_length=50)


# ══════════════════════════════════════════════════════════════════════════
# Response Schemas
# ══════════════════════════════════════════════════════════════════════════


class AthleteScanSubmitResponse(BaseModel):
    """POST /athlete-classifications/scan 返回体."""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    status: str  # pending | running | success | failed


class AthleteScanStatusResponse(BaseModel):
    """GET /athlete-classifications/scan/{task_id} 返回体."""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    status: str
    scanned: int | None = None
    inserted: int | None = None
    updated: int | None = None
    skipped: int | None = None
    errors: int | None = None
    elapsed_s: float | None = None
    error_detail: str | None = None


class AthleteClassificationItem(BaseModel):
    """GET /athlete-classifications 列表元素."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cos_object_key: str
    athlete_id: UUID
    athlete_name: str
    name_source: str           # 'map' | 'fallback'
    tech_category: str
    classification_source: str  # 'rule' | 'llm' | 'fallback'
    classification_confidence: float
    preprocessed: bool
    preprocessing_job_id: UUID | None = None
    last_diagnosis_report_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class AthletePreprocessingSubmitResponse(BaseModel):
    """POST /tasks/athlete-preprocessing 单条返回体."""

    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    athlete_video_classification_id: UUID
    cos_object_key: str
    status: str
    reused: bool
    segment_count: int | None = None
    has_audio: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AthleteBatchSubmittedItem(BaseModel):
    """批量提交成功条目."""

    model_config = ConfigDict(from_attributes=True)

    athlete_video_classification_id: UUID
    job_id: UUID | None = None
    task_id: UUID | None = None
    reused: bool = False


class AthleteBatchRejectedItem(BaseModel):
    """批量提交失败条目（不阻断批次）."""

    model_config = ConfigDict(extra="forbid")

    athlete_video_classification_id: UUID
    error_code: str
    message: str


class AthleteBatchSubmitResponse(BaseModel):
    """批量提交统一返回体（预处理 / 诊断共用）."""

    model_config = ConfigDict(extra="forbid")

    submitted: list[AthleteBatchSubmittedItem]
    rejected: list[AthleteBatchRejectedItem]


class AthleteDiagnosisSubmitResponse(BaseModel):
    """POST /tasks/athlete-diagnosis 单条返回体."""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    athlete_video_classification_id: UUID
    tech_category: str
    status: str
