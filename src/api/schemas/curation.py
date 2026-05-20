"""Feature-021 — 视频内容清洗 API Pydantic schemas.

严格对齐 ``specs/021-video-content-curation/contracts/*.md``。
所有 Request 强制 ``extra="forbid"``（章程原则 IX 附加约束）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ══════════════════════════════════════════════════════════════════════════
# Request Schemas
# ══════════════════════════════════════════════════════════════════════════


class CurationSubmitRequest(BaseModel):
    """POST /api/v1/tasks/curation 单条请求体."""

    model_config = ConfigDict(extra="forbid")

    coach_video_classification_id: UUID
    curation_rubric_version: str | None = Field(
        None,
        pattern=r"^v[0-9]+$",
        description="形如 'v1'；不传则取当前最高版本",
    )
    force: bool = False


class CurationBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coach_video_classification_id: UUID


class CurationBatchRequest(BaseModel):
    """POST /api/v1/tasks/curation 批量请求体（``{items: [...]}``）.

    本路由通过 ``items`` 字段在路径上区分单条 / 批量；与 F-013 / F-020 同惯例。
    """

    model_config = ConfigDict(extra="forbid")

    items: list[CurationBatchItem] = Field(..., min_length=1, max_length=100)
    curation_rubric_version: str | None = Field(None, pattern=r"^v[0-9]+$")
    force: bool = False


# ══════════════════════════════════════════════════════════════════════════
# Response Schemas
# ══════════════════════════════════════════════════════════════════════════


class CurationSubmitResponse(BaseModel):
    """单条提交响应（contracts/submit_curation.md 单条段落）。"""

    job_id: UUID
    task_id: UUID | None
    cos_object_key: str
    curation_rubric_version: str
    status: str
    queued: bool
    idempotent_short_circuit: bool


class CurationBatchSubmittedItem(BaseModel):
    coach_video_classification_id: UUID
    job_id: UUID | None
    task_id: UUID | None
    queued: bool
    idempotent_short_circuit: bool


class CurationBatchRejectedItem(BaseModel):
    coach_video_classification_id: UUID
    error_code: str
    message: str


class CurationBatchResponse(BaseModel):
    """批量提交响应（contracts/submit_curation.md 批量段落）。"""

    submitted: list[CurationBatchSubmittedItem]
    rejected: list[CurationBatchRejectedItem]


# ── GET /curation-jobs/{id} 响应 ─────────────────────────────────────


class CurationSegmentItem(BaseModel):
    """逐分段判定 + 覆盖留痕（与 ORM 行字段对应）。"""

    segment_index: int
    segment_start_ms: int
    segment_end_ms: int
    auto_decision: str
    validity_score: float
    rejection_reason: str | None
    decision_source: str
    dim_breakdown: dict[str, Any] | None
    override_decision: str | None
    override_user: str | None
    override_reason: str | None
    overridden_at: datetime | None
    effective_decision: str


class CurationJobSummary(BaseModel):
    """视频级清洗摘要 + 派生标记（spec FR-004 + FR-009）。"""

    total_segment_count: int | None
    accepted_segment_count: int | None
    rejected_segment_count: int | None
    uncertain_segment_count: int | None
    total_duration_seconds: float | None
    accepted_duration_seconds: float | None
    accepted_duration_ratio: float | None
    low_quality: bool | None
    audio_unavailable: bool | None
    short_video: bool | None
    has_overrides: bool
    kb_stale_after_override: bool


class CurationJobDetail(BaseModel):
    """GET /api/v1/curation-jobs/{id} 响应载荷。"""

    job_id: UUID
    cos_object_key: str
    coach_video_classification_id: UUID
    preprocessing_job_id: UUID
    curation_rubric_version: str
    status: str
    error_code: str | None
    error_message: str | None
    summary: CurationJobSummary
    submitted_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    segments: list[CurationSegmentItem]


# ── PATCH /curation-jobs/{job_id}/segments/{segment_index} ─────────


class CurationOverrideRequest(BaseModel):
    """人工覆盖请求体（contracts/override_curation_segment.md）.

    取消覆盖：传 ``override_decision: null`` + 任意（可空）reason；
    新增 / 修改覆盖：传 ``"accepted"`` / ``"rejected"`` + 必填 reason。
    """

    model_config = ConfigDict(extra="forbid")

    override_decision: str | None = Field(
        ...,
        description="'accepted' | 'rejected' | null（取消覆盖）",
    )
    override_reason: str | None = Field(
        None,
        max_length=1000,
        description="覆盖理由；override_decision != null 时必填",
    )
    override_user: str = Field(
        ..., min_length=1, max_length=64,
        description="操作员标识；目前为字符串字段，未来接入鉴权时改读上下文",
    )


class CurationOverrideRecomputed(BaseModel):
    """覆盖后重算的关键摘要字段（响应内嵌；完整摘要可从 GET 接口取）。"""

    accepted_segment_count: int
    rejected_segment_count: int
    accepted_duration_ratio: float
    low_quality: bool
    kb_stale_after_override: bool


class CurationOverrideResponse(BaseModel):
    """单分段人工覆盖响应（contracts/override_curation_segment.md）。"""

    job_id: UUID
    segment_index: int
    auto_decision: str
    override_decision: str | None
    override_user: str | None
    override_reason: str | None
    overridden_at: datetime | None
    effective_decision: str
    summary_recomputed: CurationOverrideRecomputed


# ── GET /curation-stats（US5 P3） ─────────────────────────────────────


class CurationStatsItem(BaseModel):
    """跨教练 / 类别 / 规范版本的清洗有效率聚合项（contracts/curation_stats.md）.

    根据 ``group_by`` 不同，三个分组键字段（``coach_name`` / ``tech_category`` /
    ``curation_rubric_version``）按需置非空：

    - ``group_by=coach`` ⇒ 仅 ``coach_name`` 非空
    - ``group_by=tech_category`` ⇒ 仅 ``tech_category`` 非空
    - ``group_by=rubric_version`` ⇒ 仅 ``curation_rubric_version`` 非空

    汇总指标（视频粒度）：

    - ``video_count``：本组内视频数（distinct cos_object_key）
    - ``avg_accepted_duration_ratio``：组内 ``accepted_duration_ratio`` 算术均值
    - ``avg_validity_score``：组内逐分段 ``validity_score`` 均值（以分段为权）
    - ``low_quality_video_count``：``low_quality=true`` 的视频数
    - ``with_overrides_video_count``：含至少一条覆盖记录的视频数（仅 group_by=coach
      / tech_category 时填）

    样本量保护：``video_count < 5`` 时附 ``low_sample=true``，避免聚合可信度被拉偏
    （contracts/curation_stats.md § 行为契约 4）。
    """

    model_config = ConfigDict(extra="forbid")

    coach_name: str | None = None
    tech_category: str | None = None
    curation_rubric_version: str | None = None
    video_count: int = Field(..., ge=0)
    avg_accepted_duration_ratio: float | None = None
    avg_validity_score: float | None = None
    low_quality_video_count: int = Field(0, ge=0)
    with_overrides_video_count: int | None = None
    low_sample: bool = False


__all__ = [
    "CurationSubmitRequest",
    "CurationBatchItem",
    "CurationBatchRequest",
    "CurationSubmitResponse",
    "CurationBatchSubmittedItem",
    "CurationBatchRejectedItem",
    "CurationBatchResponse",
    "CurationSegmentItem",
    "CurationJobSummary",
    "CurationJobDetail",
    "CurationOverrideRequest",
    "CurationOverrideRecomputed",
    "CurationOverrideResponse",
    "CurationStatsItem",
]
