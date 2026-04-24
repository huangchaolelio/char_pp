"""ClassificationGateService — pre-check that a coach video has been classified.

Feature 013 US1/FR-004a: before enqueueing a ``kb_extraction`` task for a COS
object, we require that ``coach_video_classifications.tech_category`` is set to
a real category (not NULL, not 'unclassified'). Otherwise the submission is
rejected with ``CLASSIFICATION_REQUIRED`` so the caller can first run
``POST /api/v1/tasks/classification``.

The service is intentionally small — one query, one boolean — and is used by
the ``tasks`` router on every kb-extraction submission (single and batch).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.coach_video_classification import CoachVideoClassification


class ClassificationGateService:
    """Guard kb_extraction submissions against unclassified videos."""

    UNCLASSIFIED = "unclassified"

    async def check_classified(
        self, session: AsyncSession, cos_object_key: str
    ) -> bool:
        """Return True if the COS object has a real (non-unclassified) tech_category.

        Returns False when:
          - There is no ``coach_video_classifications`` row for this key.
          - The row's ``tech_category`` is NULL or 'unclassified'.
        """
        row = (
            await session.execute(
                select(CoachVideoClassification.tech_category).where(
                    CoachVideoClassification.cos_object_key == cos_object_key
                )
            )
        ).scalar_one_or_none()
        if row is None or not row or row == self.UNCLASSIFIED:
            return False
        return True

    async def get_tech_category(
        self, session: AsyncSession, cos_object_key: str
    ) -> str | None:
        """Return the tech_category (or None). Helper for richer error messages."""
        row = (
            await session.execute(
                select(CoachVideoClassification.tech_category).where(
                    CoachVideoClassification.cos_object_key == cos_object_key
                )
            )
        ).scalar_one_or_none()
        return row
