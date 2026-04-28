"""DiagnosisService — orchestrates the full amateur motion diagnosis flow.

Flow:
  1. Validate tech_category against ActionType enum
  2. Query active TechStandard (StandardNotFoundError if none)
  3. Localize video to temp file (COS download or local path)
  4. Extract pose frames via pose_estimator.estimate_pose()
  5. Compute dimension measurements via tech_extractor
  6. Compare measured values against standard points (diagnosis_scorer)
  7. Generate LLM improvement advice for deviant dimensions
  8. Compute overall score
  9. Persist DiagnosisReport + DiagnosisDimensionResult to DB
  10. Return DiagnosisReportData
  11. Cleanup temp file (finally)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from src.utils.time_utils import now_cst
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.diagnosis_report import (
    DeviationDirection,
    DeviationLevel,
    DiagnosisDimensionResult,
    DiagnosisReport,
)
from src.models.expert_tech_point import ActionType
from src.models.tech_standard import StandardStatus, TechStandard
from src.services.diagnosis_llm_advisor import generate_improvement_advice
from src.services.diagnosis_scorer import (
    DimensionScore,
    compute_dimension_score,
    compute_overall_score,
)
from src.services.llm_client import LlmClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class StandardNotFoundError(Exception):
    """Raised when no active TechStandard exists for the requested tech_category."""

    def __init__(self, tech_category: str) -> None:
        super().__init__(f"No active standard for tech_category: {tech_category}")
        self.tech_category = tech_category


class ExtractionFailedError(Exception):
    """Raised when no valid action segments are detected in the video."""

    def __init__(self, reason: str = "No valid action segments detected in video") -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Result data class (returned from diagnose())
# ---------------------------------------------------------------------------

@dataclass
class DimensionResultData:
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


@dataclass
class DiagnosisReportData:
    report_id: uuid.UUID
    tech_category: str
    standard_id: int
    standard_version: int
    overall_score: float
    strengths: list[str]
    dimensions: list[DimensionResultData]
    created_at: datetime


# ---------------------------------------------------------------------------
# DiagnosisService
# ---------------------------------------------------------------------------

class DiagnosisService:
    """Orchestrates the full diagnosis pipeline."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def diagnose(
        self,
        tech_category: str,
        video_path: str,
    ) -> DiagnosisReportData:
        """Run full diagnosis and return a DiagnosisReportData.

        Raises:
            StandardNotFoundError: if no active standard for tech_category.
            ExtractionFailedError: if video yields no valid measurements.
        """
        import time
        start_ms = int(time.monotonic() * 1000)

        # 1. Validate tech_category
        if tech_category not in {at.value for at in ActionType}:
            raise ValueError(f"{tech_category!r} is not a valid tech category")

        # 2. Load active standard
        standard = await self._get_active_standard(tech_category)

        tmp_path: Optional[str] = None
        try:
            # 3. Localize video
            tmp_path = await self._localize_video(video_path)

            # 4+5. Extract dimension measurements
            measured_map = await self._extract_measurements(tmp_path, tech_category)

            if not measured_map:
                raise ExtractionFailedError()

            # 6. Score each dimension present in standard
            llm_client = LlmClient.from_settings()
            dim_scores: list[DimensionScore] = []
            for point in standard.points:
                if point.dimension not in measured_map:
                    continue
                measured = measured_map[point.dimension]
                ds = compute_dimension_score(
                    measured=measured,
                    std_min=point.min,
                    std_max=point.max,
                    ideal=point.ideal,
                    unit=point.unit or "",
                    dimension=point.dimension,
                )
                dim_scores.append(ds)

            if not dim_scores:
                raise ExtractionFailedError(
                    "Video processed but no dimensions matched the standard"
                )

            # 7. Generate LLM advice for deviant dimensions
            advice_map: dict[str, Optional[str]] = {}
            llm_calls = 0
            for ds in dim_scores:
                if ds.deviation_level != DeviationLevel.ok:
                    advice = await asyncio.get_event_loop().run_in_executor(
                        None,
                        generate_improvement_advice,
                        ds,
                        tech_category,
                        llm_client,
                    )
                    advice_map[ds.dimension] = advice
                    llm_calls += 1
                else:
                    advice_map[ds.dimension] = None

            # 8. Overall score
            overall_score = compute_overall_score(dim_scores)

            # Strengths = dimensions within standard
            strengths = [
                ds.dimension
                for ds in dim_scores
                if ds.deviation_level == DeviationLevel.ok
            ]

            # 9. Persist
            report = DiagnosisReport(
                tech_category=tech_category,
                standard_id=standard.id,
                standard_version=standard.version,
                video_path=video_path,
                overall_score=overall_score,
                strengths_summary=json.dumps(strengths, ensure_ascii=False),
            )
            self._session.add(report)
            await self._session.flush()  # get report.id assigned

            for ds in dim_scores:
                dim_result = DiagnosisDimensionResult(
                    report_id=report.id,
                    dimension=ds.dimension,
                    measured_value=ds.measured_value,
                    ideal_value=ds.ideal_value,
                    standard_min=ds.standard_min,
                    standard_max=ds.standard_max,
                    unit=ds.unit or None,
                    score=ds.score,
                    deviation_level=ds.deviation_level.value,
                    deviation_direction=ds.deviation_direction.value,
                    improvement_advice=advice_map.get(ds.dimension),
                )
                self._session.add(dim_result)

            await self._session.flush()

            elapsed_ms = int(time.monotonic() * 1000) - start_ms
            logger.info(
                "diagnosis_complete",
                extra={
                    "tech_category": tech_category,
                    "standard_id": standard.id,
                    "overall_score": overall_score,
                    "dimensions_count": len(dim_scores),
                    "deviations_count": len([d for d in dim_scores if d.deviation_level != DeviationLevel.ok]),
                    "llm_calls": llm_calls,
                    "elapsed_ms": elapsed_ms,
                },
            )

            # 10. Build result
            dim_results = [
                DimensionResultData(
                    dimension=ds.dimension,
                    measured_value=ds.measured_value,
                    ideal_value=ds.ideal_value,
                    standard_min=ds.standard_min,
                    standard_max=ds.standard_max,
                    unit=ds.unit or None,
                    score=ds.score,
                    deviation_level=ds.deviation_level.value,
                    deviation_direction=ds.deviation_direction.value,
                    improvement_advice=advice_map.get(ds.dimension),
                )
                for ds in dim_scores
            ]

            return DiagnosisReportData(
                report_id=report.id,
                tech_category=tech_category,
                standard_id=standard.id,
                standard_version=standard.version,
                overall_score=overall_score,
                strengths=strengths,
                dimensions=dim_results,
            created_at=report.created_at or now_cst(),
            )

        finally:
            if tmp_path and tmp_path != video_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ──────────────────────────────────────────────────────────────────────
    # Feature 013 T040: async entry point for the diagnosis Celery worker.
    # The legacy ``diagnose(tech_category, video_path)`` remains for sync
    # callers (``/api/v1/diagnosis``); this wrapper adapts the async Celery
    # submission flow where tech_category is inferred from the filename.
    # ──────────────────────────────────────────────────────────────────────
    async def diagnose_athlete_video(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        video_storage_uri: str,
        knowledge_base_version: str | None = None,
    ) -> dict:
        """Async wrapper for the diagnosis Celery task.

        Behaviour:
          * ``session`` is ignored when it matches ``self._session`` (the task
            already owns one); otherwise we temporarily swap it in.
          * ``tech_category`` is inferred from the filename via
            :class:`TechClassifier`; falls back to ``general`` when no rule
            matches (the downstream standard lookup will surface a
            ``StandardNotFoundError`` that the worker records as a failure).
          * Returns a dict suitable for the Celery result payload; the full
            :class:`DiagnosisReportData` object is persisted server-side.
        """
        if not video_storage_uri:
            raise ValueError("video_storage_uri is required")

        # Swap session if caller supplied a different one.
        prev_session = self._session
        if session is not None and session is not prev_session:
            self._session = session
        try:
            filename = video_storage_uri.rsplit("/", 1)[-1] or video_storage_uri
            course_series = (
                video_storage_uri.rsplit("/", 2)[-2]
                if "/" in video_storage_uri.rstrip("/")
                else ""
            )

            # Best-effort tech_category inference (rule match only; no LLM
            # fallback here — the sync pipeline handles ambiguity when it
            # actually processes frames).
            from src.services.tech_classifier import TechClassifier

            try:
                classifier = TechClassifier.from_settings()
                cls_result = classifier.classify(filename, course_series)
                tech_category = cls_result.tech_category
            except Exception:  # noqa: BLE001 — never block on classifier config
                tech_category = "general"

            if tech_category == "unclassified":
                tech_category = "general"

            logger.info(
                "diagnose_athlete_video: task_id=%s uri=%s inferred_category=%s kb_ver=%s",
                task_id, video_storage_uri, tech_category, knowledge_base_version,
            )

            try:
                report = await self.diagnose(
                    tech_category=tech_category, video_path=video_storage_uri
                )
            except (StandardNotFoundError, ExtractionFailedError) as exc:
                # Domain failures surface as dict payload with error — the
                # worker will still mark the task success=False via the
                # outer try/except capturing them as Exception.
                raise

            return {
                "task_id": str(task_id),
                "report_id": str(report.report_id),
                "tech_category": report.tech_category,
                "standard_version": report.standard_version,
                "overall_score": report.overall_score,
                "dimension_count": len(report.dimensions),
                "knowledge_base_version": knowledge_base_version,
            }
        finally:
            self._session = prev_session

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    async def _get_active_standard(self, tech_category: str) -> TechStandard:
        stmt = select(TechStandard).where(
            TechStandard.tech_category == tech_category,
            TechStandard.status == StandardStatus.active,
        )
        result = await self._session.execute(stmt)
        standard = result.scalar_one_or_none()
        if standard is None:
            raise StandardNotFoundError(tech_category)
        return standard

    async def _localize_video(self, video_path: str) -> str:
        """Return a local file path for the video.

        If video_path starts with 'cos://' or doesn't look like an absolute path,
        download from COS to a temp file. Otherwise return as-is.
        """
        if video_path.startswith("cos://") or (
            not os.path.isabs(video_path) and not os.path.exists(video_path)
        ):
            return await self._download_from_cos(video_path)
        return video_path

    async def _download_from_cos(self, cos_path: str) -> str:
        """Download a COS object to a temp file and return the local path."""
        from src.services.cos_client import download_to_temp

        # Strip cos:// prefix if present
        object_key = cos_path
        if cos_path.startswith("cos://"):
            # cos://bucket/path/to/key → path/to/key
            parts = cos_path[6:].split("/", 1)
            object_key = parts[1] if len(parts) > 1 else parts[0]

        tmp_path = await asyncio.get_event_loop().run_in_executor(
            None, download_to_temp, object_key
        )
        return tmp_path

    async def _extract_measurements(
        self, video_path: str, tech_category: str
    ) -> dict[str, float]:
        """Extract dimension measurements from video using the existing pipeline.

        Returns a dict mapping dimension name → measured value.
        Returns empty dict if no valid segments found.
        """
        from src.services.pose_estimator import estimate_pose
        from src.services.action_segmenter import segment_actions
        from src.services.action_classifier import classify_segments
        from src.services.tech_extractor import extract_tech_points

        # Run CPU-bound work in executor to not block event loop
        def _run_pipeline():
            frame_results = estimate_pose(video_path)
            if not frame_results:
                return {}

            segments = segment_actions(frame_results)
            if not segments:
                return {}

            classified = classify_segments(segments, target_action=tech_category)
            if not classified:
                return {}

            # Take the first matching segment's extraction result
            extraction_results = extract_tech_points(classified[:1], frame_results)
            if not extraction_results:
                return {}

            result = extraction_results[0]
            return {
                dim.dimension: dim.param_ideal
                for dim in result.dimensions
                if dim.extraction_confidence >= 0.7
            }

        return await asyncio.get_event_loop().run_in_executor(None, _run_pipeline)
