"""Pydantic schemas for task-related API requests and responses.

Aligned with contracts/api.md.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from typing import Optional

from pydantic import BaseModel, Field


# ── Request schemas ──────────────────────────────────────────────────────────

class ExpertVideoRequest(BaseModel):
    cos_object_key: str = Field(
        ...,
        description="COS 中的对象路径，如 coach-videos/forehand_lesson_001.mp4",
        examples=["coach-videos/forehand_lesson_001.mp4"],
    )
    notes: Optional[str] = Field(None, description="视频备注说明")
    # Feature 002: audio analysis options
    enable_audio_analysis: bool = Field(True, description="是否启用音频分析（Whisper）")
    audio_language: str = Field("zh", description="音频语言代码，默认 zh（普通话）")
    # US3: optional pre-declared duration for early rejection before download
    video_duration_seconds: Optional[float] = Field(
        None, description="视频时长（秒），由客户端提供时用于提前校验 90 分钟上限"
    )
    # Action type hint: when set, only keep extracted segments matching this type.
    # Auto-inferred from cos_object_key filename keywords if not provided.
    # Values: "forehand_topspin" | "backhand_push" | None (no filter)
    action_type_hint: Optional[str] = Field(
        None,
        description="动作类型提示，用于过滤提取结果。可选值: forehand_topspin / backhand_push。"
                    "不传时由系统根据视频文件名关键词自动推断。",
    )


# AthleteVideoRequest uses multipart/form-data — parsed in the endpoint directly


# ── Task status response ─────────────────────────────────────────────────────

class TaskStatusResponse(BaseModel):
    task_id: UUID
    task_type: str
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    video_duration_seconds: Optional[float] = None
    video_fps: Optional[float] = None
    video_resolution: Optional[str] = None
    # Feature 002: long video progress fields
    progress_pct: Optional[float] = None
    processed_segments: Optional[int] = None
    total_segments: Optional[int] = None
    audio_fallback_reason: Optional[str] = None


# ── Expert video result ──────────────────────────────────────────────────────

class ExtractedTechPoint(BaseModel):
    action_type: str
    dimension: str
    param_min: float
    param_max: float
    param_ideal: float
    unit: str
    extraction_confidence: float
    # Feature 002: source annotation, conflict fields, and timestamp range (FR-008)
    source_type: str = "visual"
    conflict_flag: bool = False
    conflict_detail: Optional[dict] = None
    segment_start_ms: Optional[int] = None
    segment_end_ms: Optional[int] = None


class AudioAnalysisInfo(BaseModel):
    enabled: bool
    quality_flag: Optional[str] = None
    fallback_reason: Optional[str] = None
    transcript_sentence_count: Optional[int] = None


class ConflictDetail(BaseModel):
    dimension: str
    visual_ideal: float
    audio_ideal: float
    diff_pct: float


class TaskResultExpertResponse(BaseModel):
    task_id: UUID
    knowledge_base_version_draft: Optional[str] = None
    extracted_points_count: int
    extracted_points: list[ExtractedTechPoint]
    pending_approval: bool
    # Feature 002: audio analysis summary and conflicts
    audio_analysis: Optional[AudioAnalysisInfo] = None
    conflicts: list[ConflictDetail] = []


# ── Athlete video result ─────────────────────────────────────────────────────

class DeviationItem(BaseModel):
    deviation_id: UUID
    dimension: str
    measured_value: float
    ideal_value: float
    deviation_value: float
    deviation_direction: str
    confidence: float
    is_low_confidence: bool
    is_stable_deviation: Optional[bool] = None
    impact_score: Optional[float] = None


class CoachingAdviceItem(BaseModel):
    advice_id: UUID
    dimension: str
    deviation_description: str
    improvement_target: str
    improvement_method: str
    impact_score: float
    reliability_level: str
    reliability_note: Optional[str] = None


class MotionAnalysisItem(BaseModel):
    analysis_id: UUID
    action_type: str
    segment_start_ms: int
    segment_end_ms: int
    overall_confidence: float
    is_low_confidence: bool
    deviation_report: list[DeviationItem]
    coaching_advice: list[CoachingAdviceItem]


class ResultSummary(BaseModel):
    total_actions_detected: int
    actions_analyzed: int
    actions_low_confidence: int
    total_deviations: int
    stable_deviations: int
    top_advice_dimension: Optional[str] = None


class TaskResultAthleteResponse(BaseModel):
    task_id: UUID
    knowledge_base_version: str
    motion_analyses: list[MotionAnalysisItem]
    summary: ResultSummary


# ── Submit response ──────────────────────────────────────────────────────────

class TaskSubmitResponse(BaseModel):
    task_id: UUID
    status: str
    cos_object_key: Optional[str] = None
    knowledge_base_version: Optional[str] = None
    estimated_completion_seconds: int = 300


# ── Delete response ──────────────────────────────────────────────────────────

class TaskDeleteResponse(BaseModel):
    task_id: UUID
    deleted_at: datetime
    message: str


# ── COS video list ────────────────────────────────────────────────────────────

class CosVideoItem(BaseModel):
    cos_object_key: str
    filename: str
    size_bytes: int
    action_type: str  # "forehand" | "backhand" | "forehand+backhand" | "other"


class CosVideoListResponse(BaseModel):
    action_type_filter: str
    total: int
    videos: list[CosVideoItem]
