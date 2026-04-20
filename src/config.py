from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # Tencent Cloud COS
    cos_secret_id: str
    cos_secret_key: str
    cos_region: str
    cos_bucket: str

    # Video processing
    tmp_dir: Path = Path("/tmp/coaching-advisor")

    # Application
    app_env: str = "development"
    log_level: str = "INFO"

    # Algorithm thresholds (from spec clarifications)
    confidence_threshold: float = 0.7
    min_video_fps: float = 15.0
    min_video_width: int = 854
    min_video_height: int = 480
    keypoint_visibility_threshold: float = 0.5

    # Deviation stability thresholds
    stability_min_samples: int = 3
    stability_min_occurrence_rate: float = 0.70

    # Data retention
    data_retention_months: int = 12

    # Pose estimation backend
    pose_backend: str = "auto"           # "auto" | "mediapipe" | "yolov8"
    pose_batch_size: int = 16            # YOLOv8 batch size (GPU path)
    mediapipe_model_complexity: int = 1  # MediaPipe model complexity (CPU path)

    # COS video selection — forehand / backhand keywords (comma-separated)
    cos_video_prefix: str = "charhuang/tt_video/乒乓球合集【较新】/《知行合一》孙浩泓专业乒乓球全套教学课程120集/"
    forehand_video_keywords: str = "正手"   # comma-separated substrings matched against filename
    backhand_video_keywords: str = "反手"   # comma-separated substrings matched against filename

    # Audio analysis — Feature 002
    whisper_model: str = "small"         # tiny | base | small | medium
    whisper_device: str = "auto"         # auto | cpu | cuda  (auto→cuda if available, else cpu)
    audio_keyword_file: str = "config/keywords/tech_hint_keywords.json"
    audio_priority_window_s: float = 3.0        # seconds around keyword hit to mark as priority
    audio_snr_threshold_db: float = 10.0        # below this SNR → quality_flag=low_snr
    audio_conflict_threshold_pct: float = 0.15  # param diff ratio > this → conflict_flag
    long_video_segment_duration_s: int = 180    # 3-minute chunks for long video processing
    max_video_duration_s: int = 5400            # 90 minutes hard limit


@lru_cache
def get_settings() -> Settings:
    return Settings()
