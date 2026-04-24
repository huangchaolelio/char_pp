"""KB-extraction task schemas (Feature 013 US3 — FR-002 decoupling).

A KB-extraction request only carries ``cos_object_key`` + audio toggles; it
must NOT accept diagnosis fields like ``video_storage_uri`` or
``knowledge_base_version`` (those belong to the diagnosis schema).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class KbExtractionSingleRequest(BaseModel):
    """``POST /api/v1/tasks/kb-extraction`` — single classified coach video."""

    model_config = ConfigDict(extra="forbid")

    cos_object_key: str = Field(..., min_length=1, max_length=1000)
    enable_audio_analysis: bool = True
    audio_language: str = Field("zh", max_length=10)
    force: bool = False


class KbExtractionBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KbExtractionSingleRequest] = Field(..., min_length=1)
