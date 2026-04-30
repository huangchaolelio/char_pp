"""Feature-018 — `GET /api/v1/business-workflow/overview` 响应 DTO.

对齐 specs/018-workflow-standardization/data-model.md § 7
与 contracts/business-workflow-overview.yaml。

降级档（(100 万, 1000 万] 行）响应中 p50_seconds / p95_seconds 省略；
由 WorkflowOverviewMeta.degraded=true 标识。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# 与 _phase_step_hook.BusinessStep 保持同步（Feature-020 扩容至 10 值）
BusinessStepLiteral = Literal[
    "scan_cos_videos",
    "preprocess_video",
    "classify_video",
    "extract_kb",
    "review_conflicts",
    "kb_version_activate",
    "build_standards",
    "diagnose_athlete",
    # Feature-020 — 运动员推理流水线 INFERENCE 阶段新增 2 步
    "scan_athlete_videos",
    "preprocess_athlete_video",
]

BusinessPhaseLiteral = Literal["TRAINING", "STANDARDIZATION", "INFERENCE"]


class StepSnapshot(BaseModel):
    """单个业务步骤在当前聚合窗口内的快照.

    完整档返回全部 7 个字段；降级档省略 p50_seconds / p95_seconds（None）。
    """

    model_config = ConfigDict(extra="forbid")

    step: BusinessStepLiteral
    pending: int = Field(..., ge=0)
    processing: int = Field(..., ge=0)
    success: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    recent_24h_completed: int = Field(..., ge=0)
    # 仅完整档返回；降级档为 None（Pydantic 会在 model_dump(exclude_none=True) 时省略）
    p50_seconds: float | None = None
    p95_seconds: float | None = None


class PhaseSnapshot(BaseModel):
    """单个业务阶段的快照，包含该阶段所有步骤映射."""

    model_config = ConfigDict(extra="forbid")

    phase: BusinessPhaseLiteral
    steps: dict[str, StepSnapshot]


class WorkflowOverviewSnapshot(BaseModel):
    """三阶段总览——路由 response_model data 载荷."""

    model_config = ConfigDict(extra="forbid")

    TRAINING: PhaseSnapshot
    STANDARDIZATION: PhaseSnapshot
    INFERENCE: PhaseSnapshot


class WorkflowOverviewMeta(BaseModel):
    """本 Feature 专属 meta；SuccessEnvelope.meta 接受 dict 时由路由层 model_dump 落位."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    window_hours: int = Field(..., ge=1, le=168)
    degraded: bool
    degraded_reason: Literal["row_count_exceeds_latency_budget"] | None = None


__all__ = [
    "BusinessPhaseLiteral",
    "BusinessStepLiteral",
    "StepSnapshot",
    "PhaseSnapshot",
    "WorkflowOverviewSnapshot",
    "WorkflowOverviewMeta",
]
