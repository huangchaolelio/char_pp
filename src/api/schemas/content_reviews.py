"""Feature-022 — 内容审核工作台 API Pydantic schemas.

严格对齐 ``specs/022-content-review-workflow/contracts/content-reviews.yaml``。
所有 Request 强制 ``extra="forbid"``（章程原则 IX 附加约束）。

枚举语义对齐 data-model.md § 3 / § 4：
- ReviewState: pending_review / approved / rejected / stale
- Decision: approved / rejected
- ReasonCode: quality_low / tech_irrelevant / coach_unauthorized /
              content_duplicated / other
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ══════════════════════════════════════════════════════════════════════════
# Enum
# ══════════════════════════════════════════════════════════════════════════


class ReviewState(str, Enum):
    """``coach_video_classifications.review_state`` 取值."""

    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    stale = "stale"


class Decision(str, Enum):
    """``content_review_decisions.decision`` 取值（不含 stale）."""

    approved = "approved"
    rejected = "rejected"


class ReasonCode(str, Enum):
    """``content_review_decisions.reason_code`` 取值（仅 rejected 决策需要）."""

    quality_low = "quality_low"
    tech_irrelevant = "tech_irrelevant"
    coach_unauthorized = "coach_unauthorized"
    content_duplicated = "content_duplicated"
    other = "other"


# ══════════════════════════════════════════════════════════════════════════
# Response Schemas
# ══════════════════════════════════════════════════════════════════════════


class ReviewDecision(BaseModel):
    """单条决策记录（``content_review_decisions`` 行的对外投影）."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    decision: Decision
    reason_code: Optional[ReasonCode] = None
    note: Optional[str] = Field(None, max_length=1000)
    reviewer_id: str = Field(..., max_length=64)
    decided_at: datetime
    cleansing_version: Optional[UUID] = None
    superseded_at: Optional[datetime] = None


class ContentReviewItem(BaseModel):
    """列表项 — EP-1 ``GET /content-reviews`` 与 EP-2 详情共用基础结构."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    coach_name: str
    tech_category: str
    cos_object_key: str
    filename: str
    review_state: ReviewState
    review_version: int
    pending_since: Optional[datetime] = None
    cleansing_version: Optional[UUID] = None
    last_decision: Optional[ReviewDecision] = None


class CurationSegmentSample(BaseModel):
    """详情接口附带的清洗摘要分段样例（最多 5 条）."""

    model_config = ConfigDict(from_attributes=True)

    start_seconds: float
    end_seconds: float
    transcript_excerpt: str


class CurationSummary(BaseModel):
    """详情接口附带的清洗摘要（取自 ``video_curation_jobs`` 最近一次成功作业）."""

    model_config = ConfigDict(from_attributes=True)

    total_segments: int
    accepted_segments: int
    rejected_segments: int
    accepted_duration_ratio: float
    sample_segments: list[CurationSegmentSample] = Field(
        default_factory=list, max_length=5
    )


class ContentReviewDetail(ContentReviewItem):
    """EP-2 ``GET /content-reviews/{cvclf_id}`` 详情响应载荷."""

    curation_summary: Optional[CurationSummary] = None
    decision_history: list[ReviewDecision] = Field(default_factory=list)


# ── 统计 ─────────────────────────────────────────────────────────────────


class ReviewerThroughput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reviewer_id: str
    decisions: int


class ReasonBreakdown(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reason_code: ReasonCode
    count: int


class StatsResponse(BaseModel):
    """EP-4 ``GET /content-reviews/stats`` 响应载荷."""

    model_config = ConfigDict(from_attributes=True)

    # 注意：契约中字段名为 ``from`` / ``to``，是 Python 关键字；用 alias 暴露
    from_: datetime = Field(..., alias="from")
    to: datetime
    total: int
    approved: int
    rejected: int
    approval_rate: float = Field(
        ..., description="approved / total（total=0 时返回 0.0）"
    )
    avg_latency_seconds: Optional[float] = None
    per_reviewer: list[ReviewerThroughput] = Field(default_factory=list)
    per_reason: list[ReasonBreakdown] = Field(default_factory=list)


# ── 审核门开关 ────────────────────────────────────────────────────────────


class ReviewGateConfig(BaseModel):
    """EP-5a ``GET /admin/review-gate`` / EP-5b PATCH 响应载荷."""

    model_config = ConfigDict(from_attributes=True)

    enabled: bool = Field(
        ...,
        description="true=严格审核门（默认），false=绕过模式",
    )
    last_toggled_at: Optional[datetime] = None
    last_toggled_by: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════
# Request Schemas
# ══════════════════════════════════════════════════════════════════════════


class DecisionSubmitRequest(BaseModel):
    """EP-3 ``POST /content-reviews/{cvclf_id}/decisions`` 请求体."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason_code: Optional[ReasonCode] = None
    note: Optional[str] = Field(None, max_length=1000)
    reviewer_id: str = Field(..., max_length=64)
    expected_review_version: int = Field(
        ...,
        ge=0,
        description=(
            "乐观锁版本号；必须等于服务端当前 review_version；"
            "不一致返回 409 REVIEW_VERSION_CONFLICT"
        ),
    )


class ReviewGatePatchRequest(BaseModel):
    """EP-5b ``PATCH /admin/review-gate`` 请求体."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    operator_id: str = Field(..., max_length=64)
    reason: str = Field(..., max_length=500)


__all__ = [
    # Enum
    "ReviewState",
    "Decision",
    "ReasonCode",
    # Response
    "ReviewDecision",
    "ContentReviewItem",
    "CurationSegmentSample",
    "CurationSummary",
    "ContentReviewDetail",
    "ReviewerThroughput",
    "ReasonBreakdown",
    "StatsResponse",
    "ReviewGateConfig",
    # Request
    "DecisionSubmitRequest",
    "ReviewGatePatchRequest",
]
