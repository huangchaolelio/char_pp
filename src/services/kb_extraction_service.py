"""KbExtractionService — knowledge-base extraction stub for single classified video.

Scope of this service (Feature 013 T038):
  * Pre-condition: the video has a row in ``coach_video_classifications`` with
    a non-unclassified ``tech_category`` (enforced at submit time by
    :class:`ClassificationGateService`).
  * Post-action: mark that row's ``kb_extracted`` flag True so downstream
    queries (``GET /classifications?kb_extracted=false``) skip it.
  * Return a dict summary for the Celery task result payload.

The heavy legacy 11-step pipeline (video download → pose → ASR → LLM tips →
merge → KB version commit) is out of scope for Feature 013 and will be
reconstructed in a follow-up feature. This service keeps the pipeline
**unblocked** (kb_extraction channel accepts work and completes it), which
satisfies FR-002 physical decoupling — classification/kb_extraction/diagnosis
can crash, stop, or be redeployed independently.
"""

from __future__ import annotations

import logging
from src.utils.time_utils import now_cst

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.coach_video_classification import CoachVideoClassification

logger = logging.getLogger(__name__)


class KbExtractionService:
    """Mark a classified video as KB-extracted.

    This is a thin implementation; future work will plug in full audio/LLM
    tip extraction. For now, flipping ``kb_extracted=True`` is the visible
    side-effect that completes the task.
    """

    async def extract_knowledge(
        self,
        session: AsyncSession,
        cos_object_key: str,
        enable_audio_analysis: bool = True,
        audio_language: str = "zh",
    ) -> dict:
        """Mark the classification row as kb-extracted; return summary.

        Raises:
            ValueError: when the referenced row does not exist or is not
                classified (the caller should have gated on this already,
                but we double-check to fail safely).
        """
        if not cos_object_key:
            raise ValueError("cos_object_key is required")

        row = (
            await session.execute(
                select(CoachVideoClassification).where(
                    CoachVideoClassification.cos_object_key == cos_object_key
                )
            )
        ).scalar_one_or_none()

        if row is None:
            raise ValueError(
                f"no classification row found for cos_object_key={cos_object_key!r}"
            )
        if not row.tech_category or row.tech_category == "unclassified":
            raise ValueError(
                f"video {cos_object_key!r} is not classified (tech_category="
                f"{row.tech_category!r}); classify before KB extraction"
            )

        # Flip the flag — idempotent: re-extraction is a no-op from the DB's
        # perspective (row.kb_extracted stays True, updated_at bumps).
        row.kb_extracted = True
        row.updated_at = now_cst()
        await session.commit()

        logger.info(
            "extract_knowledge: key=%s category=%s audio=%s lang=%s → kb_extracted=True",
            cos_object_key, row.tech_category, enable_audio_analysis, audio_language,
        )
        return {
            "cos_object_key": cos_object_key,
            "tech_category": row.tech_category,
            "kb_extracted": True,
            "enable_audio_analysis": enable_audio_analysis,
            "audio_language": audio_language,
        }
