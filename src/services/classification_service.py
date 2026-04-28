"""ClassificationService — single coach video classification (Feature 013 T036).

Split out of the legacy ``expert_video_task`` monolith so the ``classify_video``
Celery task stays thin.  Given a COS object key:

  1. Derive filename + course_series from the key.
  2. Delegate to :class:`TechClassifier` (keyword rules → LLM fallback).
  3. Upsert the result into ``coach_video_classifications``.
  4. Return the resulting ``tech_category``.

This module **does not** download the video — classification operates on the
filename + course series alone (matches how Feature-008's scanner works).
"""

from __future__ import annotations

import logging
from src.utils.time_utils import now_cst
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.coach_video_classification import CoachVideoClassification
from src.services.tech_classifier import ClassificationResult, TechClassifier

logger = logging.getLogger(__name__)


def _course_series_from_key(cos_object_key: str) -> str:
    """Extract the course series (parent directory) from a COS object key."""
    parts = cos_object_key.rsplit("/", 2)
    # e.g. "charhuang/tt_video/合集/教练A/01_正手攻球.mp4" → "教练A"
    if len(parts) >= 2:
        return parts[-2]
    return ""


def _filename_from_key(cos_object_key: str) -> str:
    return cos_object_key.rsplit("/", 1)[-1] if cos_object_key else ""


class ClassificationService:
    """Classify a single coach video into a ``tech_category`` and persist it."""

    def __init__(self, classifier: TechClassifier | None = None) -> None:
        self._classifier = classifier or TechClassifier.from_settings()

    async def classify_single_video(
        self,
        session: AsyncSession,
        cos_object_key: str,
        *,
        coach_name: Optional[str] = None,
    ) -> str:
        """Classify and persist. Returns the assigned ``tech_category``.

        If a ``coach_video_classifications`` row already exists for this key,
        its ``tech_category`` / ``tech_tags`` / ``confidence`` fields are
        refreshed in place and ``updated_at`` bumped; ``kb_extracted`` is left
        untouched (a reclassification does not invalidate completed KB work).
        """
        if not cos_object_key:
            raise ValueError("cos_object_key is required")

        filename = _filename_from_key(cos_object_key)
        course_series = _course_series_from_key(cos_object_key)

        result: ClassificationResult = self._classifier.classify(filename, course_series)
        logger.info(
            "classify_single_video: key=%s → category=%s source=%s conf=%.2f",
            cos_object_key, result.tech_category, result.classification_source,
            result.confidence,
        )

        now = now_cst()
        existing = (
            await session.execute(
                select(CoachVideoClassification).where(
                    CoachVideoClassification.cos_object_key == cos_object_key
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.tech_category = result.tech_category
            existing.tech_tags = result.tech_tags or []
            existing.raw_tech_desc = result.raw_tech_desc
            existing.classification_source = result.classification_source
            existing.confidence = float(result.confidence)
            existing.updated_at = now
            # coach_name update is optional — only overwrite when caller passed one.
            if coach_name:
                existing.coach_name = coach_name
        else:
            session.add(
                CoachVideoClassification(
                    id=uuid4(),
                    coach_name=coach_name or "unknown",
                    course_series=course_series or "unknown",
                    cos_object_key=cos_object_key,
                    filename=filename,
                    tech_category=result.tech_category,
                    tech_tags=result.tech_tags or [],
                    raw_tech_desc=result.raw_tech_desc,
                    classification_source=result.classification_source,
                    confidence=float(result.confidence),
                    name_source="fallback" if not coach_name else "map",
                    kb_extracted=False,
                    created_at=now,
                    updated_at=now,
                )
            )

        await session.commit()
        return result.tech_category
