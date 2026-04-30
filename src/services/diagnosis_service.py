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
        *,
        athlete_cos_object_key: str | None = None,
        athlete_preprocessing_job_id: uuid.UUID | None = None,
        athlete_source: str | None = None,
    ) -> DiagnosisReportData:
        """Run full diagnosis and return a DiagnosisReportData.

        Feature-020 `athlete_*` kwargs（可选，仅运动员侧链路使用）:
          - athlete_cos_object_key: 写入 DiagnosisReport.cos_object_key 反查锚点
          - athlete_preprocessing_job_id: 写入 DiagnosisReport.preprocessing_job_id
          - athlete_source: 写入 DiagnosisReport.source（'athlete_pipeline' / 'legacy'）

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
                # Feature-020 三要素锚点（仅运动员链路非 None）
                cos_object_key=athlete_cos_object_key,
                preprocessing_job_id=athlete_preprocessing_job_id,
                source=athlete_source or "legacy",
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

    # ──────────────────────────────────────────────────────────────────────
    # Feature-020 · 基于 classification_id 的运动员诊断入口（T039）
    # 不影响既有 diagnose_athlete_video(uri) 入口；按 classification 查回 cos/job.
    # ──────────────────────────────────────────────────────────────────────
    async def diagnose_athlete_by_classification_id(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        classification_id: uuid.UUID,
        *,
        force: bool = False,
    ) -> dict:
        """Feature-020 US3 runner.

        Flow:
          1. Load AthleteVideoClassification row → cos_object_key + tech_category
             + preprocessing_job_id
          2. Call self.diagnose(tech_category, video_path=cos_object_key, athlete_* kwargs)
          3. Upsert ``athlete_video_classifications.last_diagnosis_report_id``
          4. Return summary dict for Celery result payload
        """
        from sqlalchemy import update

        from src.api.errors import AppException, ErrorCode
        from src.models.athlete_video_classification import AthleteVideoClassification

        prev_session = self._session
        if session is not None and session is not prev_session:
            self._session = session

        try:
            row = (
                await self._session.execute(
                    select(AthleteVideoClassification).where(
                        AthleteVideoClassification.id == classification_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise AppException(
                    ErrorCode.ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND,
                    details={"resource_id": str(classification_id)},
                )

            cos_key: str = row.cos_object_key
            tech_category: str = row.tech_category
            preprocessing_job_id = row.preprocessing_job_id

            try:
                report = await self.diagnose(
                    tech_category=tech_category,
                    video_path=cos_key,
                    athlete_cos_object_key=cos_key,
                    athlete_preprocessing_job_id=preprocessing_job_id,
                    athlete_source="athlete_pipeline",
                )
            except ExtractionFailedError as exc:
                # Feature-020: pose / extraction 全帧失败 → 专属错误码
                raise AppException(
                    ErrorCode.ATHLETE_VIDEO_POSE_UNUSABLE,
                    details={
                        "athlete_video_classification_id": str(classification_id),
                        "reason": exc.reason,
                    },
                ) from exc
            except StandardNotFoundError as exc:
                raise AppException(
                    ErrorCode.STANDARD_NOT_AVAILABLE,
                    details={"tech_category": exc.tech_category},
                ) from exc

            # Upsert last_diagnosis_report_id
            await self._session.execute(
                update(AthleteVideoClassification)
                .where(AthleteVideoClassification.id == classification_id)
                .values(last_diagnosis_report_id=report.report_id)
            )
            await self._session.commit()

            return {
                "task_id": str(task_id),
                "report_id": str(report.report_id),
                "athlete_video_classification_id": str(classification_id),
                "tech_category": report.tech_category,
                "standard_version": report.standard_version,
                "overall_score": report.overall_score,
                "dimension_count": len(report.dimensions),
            }
        finally:
            self._session = prev_session

    # ──────────────────────────────────────────────────────────────────────

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
        """Download a COS object to a temp file and return the local path.

        URI 解析约定（与分类 / 预处理 / KB 提取通道保持一致）：
          - 项目的 COS bucket 来自 ``.env::COS_BUCKET``（单租户），对象 key
            本身的第一段（如 ``charhuang/``）属于 key，不是 S3 风格的 bucket。
          - 输入允许两种等价形式：
              * ``cos://charhuang/tt_video/foo.mp4`` — 仅剥 ``cos://`` scheme
              * ``charhuang/tt_video/foo.mp4``     — 裸 key，直接下载
            二者落到 ``download_to_temp`` 的 object key 完全相同。
        """
        from src.services.cos_client import download_to_temp

        object_key = cos_path[6:] if cos_path.startswith("cos://") else cos_path

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
        from src.services.action_segmenter import (
            frames_for_segment,
            segment_actions,
        )
        from src.services.action_classifier import classify_segment
        from src.services.tech_extractor import extract_tech_points

        # Run CPU-bound work in executor to not block event loop
        def _run_pipeline():
            frame_results = estimate_pose(video_path)
            if not frame_results:
                return {}

            segments = segment_actions(frame_results)
            if not segments:
                return {}

            # v1 action_classifier 只提供单段 API ``classify_segment``；遍历并
            # 过滤出目标 tech_category。未命中目标时沿用段序第一条作为兜底，
            # 与 v1 "best effort" 语义一致。
            classified_all = [
                classify_segment(frames_for_segment(frame_results, seg), seg)
                for seg in segments
            ]
            matched = [c for c in classified_all if c.action_type == tech_category]
            classified = matched or classified_all
            if not classified:
                return {}

            # extract_tech_points 是单对象 API（ClassifiedSegment → ExtractionResult）
            result = extract_tech_points(classified[0], frame_results)
            if not result or not result.dimensions:
                return {}

            return {
                dim.dimension: dim.param_ideal
                for dim in result.dimensions
                if dim.extraction_confidence >= 0.7
            }

        return await asyncio.get_event_loop().run_in_executor(None, _run_pipeline)
