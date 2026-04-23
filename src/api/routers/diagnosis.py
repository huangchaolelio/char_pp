"""Diagnosis API router — /api/v1/diagnosis.

Endpoints:
  POST /diagnosis   Submit a video for motion diagnosis (synchronous, FR-011)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.models.expert_tech_point import ActionType
from src.services.diagnosis_service import (
    DiagnosisReportData,
    DiagnosisService,
    ExtractionFailedError,
    StandardNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnosis", tags=["diagnosis"])

_VALID_ACTION_TYPES: set[str] = {at.value for at in ActionType}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class DiagnosisRequest(BaseModel):
    tech_category: str
    video_path: str

    @field_validator("tech_category")
    @classmethod
    def validate_tech_category(cls, v: str) -> str:
        if v not in _VALID_ACTION_TYPES:
            raise ValueError(f"{v!r} is not a valid tech category")
        return v


class DimensionResultResponse(BaseModel):
    dimension: str
    measured_value: float
    ideal_value: float
    standard_min: float
    standard_max: float
    unit: Optional[str]
    score: float
    deviation_level: str
    deviation_direction: Optional[str]
    improvement_advice: Optional[str]


class DiagnosisResponse(BaseModel):
    report_id: str
    tech_category: str
    standard_id: int
    standard_version: int
    overall_score: float
    strengths: List[str]
    dimensions: List[DimensionResultResponse]
    created_at: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("", response_model=DiagnosisResponse)
async def create_diagnosis(
    request: DiagnosisRequest,
    session: AsyncSession = Depends(get_db),
):
    """Submit a video for motion diagnosis.

    Synchronous: blocks until full report is generated (FR-011).
    Returns 200 with DiagnosisResponse on success.
    Returns 404 if no active standard exists for the requested tech_category.
    Returns 400 if video cannot be processed.
    """
    service = DiagnosisService(session)

    try:
        report_data: DiagnosisReportData = await service.diagnose(
            tech_category=request.tech_category,
            video_path=request.video_path,
        )
    except StandardNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "standard_not_found",
                "detail": str(exc),
            },
        )
    except ExtractionFailedError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "extraction_failed",
                "detail": str(exc),
            },
        )
    except Exception as exc:
        logger.exception("Unexpected error during diagnosis", exc_info=exc)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "detail": "An unexpected error occurred during diagnosis.",
            },
        )

    await session.commit()

    return DiagnosisResponse(
        report_id=str(report_data.report_id),
        tech_category=report_data.tech_category,
        standard_id=report_data.standard_id,
        standard_version=report_data.standard_version,
        overall_score=report_data.overall_score,
        strengths=report_data.strengths,
        dimensions=[
            DimensionResultResponse(
                dimension=d.dimension,
                measured_value=d.measured_value,
                ideal_value=d.ideal_value,
                standard_min=d.standard_min,
                standard_max=d.standard_max,
                unit=d.unit,
                score=d.score,
                deviation_level=d.deviation_level,
                deviation_direction=d.deviation_direction,
                improvement_advice=d.improvement_advice,
            )
            for d in report_data.dimensions
        ],
        created_at=report_data.created_at.isoformat(),
    )
