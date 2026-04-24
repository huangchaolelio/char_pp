"""Diagnosis task schemas (Feature 013 US3 — FR-002 decoupling).

A diagnosis request carries an ``video_storage_uri`` (athlete-uploaded clip),
optionally a ``knowledge_base_version`` to pin. It must NOT accept
``cos_object_key`` / ``audio_language`` (those belong to classification or
kb-extraction pipelines).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DiagnosisSingleRequest(BaseModel):
    """``POST /api/v1/tasks/diagnosis`` — single athlete video."""

    model_config = ConfigDict(extra="forbid")

    video_storage_uri: str = Field(..., min_length=1, max_length=1000)
    knowledge_base_version: Optional[str] = Field(None, max_length=20)
    force: bool = False


class DiagnosisBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DiagnosisSingleRequest] = Field(..., min_length=1)
