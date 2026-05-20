"""download_video executor (Feature 014, Feature-016 US2 rewrite, Feature-021 curation gate).

Old behavior (pre-US2): downloaded the full ``video.mp4`` from COS into the job
directory. Whole-video pose / Whisper inference then ran on a single 10-min
file — ran into OOM on the 64 GB pod.

New behavior (US2): consumes the output of a successful ``video_preprocessing``
job:
  - Loads the preprocessing job + its segments via ``preprocessing_service``;
  - **Feature-021**: filters ``view.segments`` against
    ``video_curation_segment_results.effective_decision='accepted'`` —
    only accepted segments enter ``head_object`` + download. Rejected /
    uncertain segments are dropped at this step and propagated through
    downstream pose / audio / kb_extract via the smaller segment count.
    When ``settings.kb_extraction_bypass_curation_gate=True``, this filter
    is skipped (emergency rollback per business-workflow.md § 10);
    ``output_summary.curation_bypass=true``留痕。
  - **Feature-021 LOW_QUALITY_SKIP**: 若清洗作业 ``accepted_duration_ratio==0``
    （即所有分段都不通过），整个 KB 抽取作业以
    ``RuntimeError("LOW_QUALITY_SKIP: ...")`` 短路，由 orchestrator finalize
    阶段写入 ``extraction_jobs.error_code='LOW_QUALITY_SKIP'``。
  - head_object-checks every (filtered) segment + audio.wav (fail-fast with
    ``SEGMENT_MISSING:`` / ``AUDIO_MISSING:`` prefix on any gap);
  - For each segment: if the preprocessing worker left a matching local copy
    under ``${EXTRACTION_ARTIFACT_ROOT}/preprocessing/{pp_job_id}/segments/``,
    hard-link / copy it into the KB job dir; otherwise download from COS.
  - Same for audio.wav.
  - ``output_artifact_path`` → the KB job directory (NOT a single file). Down-
    stream executors (pose_analysis / audio_transcription) treat it as a dir
    and look for ``segments/seg_NNNN.mp4`` + ``audio.wav`` inside.

``output_summary`` exposes:
  - ``video_preprocessing_job_id``  (UUID of the source preprocessing job)
  - ``segments_total`` / ``segments_downloaded``
  - ``audio_downloaded``  (True iff we placed audio.wav in the KB job dir)
  - ``local_cache_hits`` / ``cos_downloads``  (observability)
  - **Feature-021** ``curation_job_id`` / ``curation_rubric_version``
  - **Feature-021** ``segments_skipped_by_curation`` / ``curation_warning``
  - **Feature-021** ``curation_bypass`` (true iff bypass switch was on)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.extraction_job import ExtractionJob
from src.models.pipeline_step import PipelineStep, PipelineStepStatus
from src.services import cos_client as _cos_mod
from src.services import preprocessing_service as _preprocessing_service
from src.services.kb_extraction_pipeline.error_codes import (
    AUDIO_MISSING,
    SEGMENT_MISSING,
    format_error,
)


logger = logging.getLogger(__name__)


# ── Module-level helpers (monkeypatch targets) ──────────────────────────────

async def _load_preprocessing_view(session: AsyncSession, cos_object_key: str):
    """Load the success preprocessing job for *cos_object_key*, including segments.

    Raises:
        RuntimeError: No success preprocessing job exists for this key.
    """
    row = await _preprocessing_service._fetch_success_job(session, cos_object_key)
    if row is None:
        raise RuntimeError(
            f"no success preprocessing job for cos_object_key={cos_object_key!r}; "
            "run POST /api/v1/tasks/preprocessing first"
        )
    view = await _preprocessing_service.get_job_view(session, row.id)
    if view is None:  # pragma: no cover — defensive
        raise RuntimeError(f"preprocessing job {row.id} view unavailable")
    return view


def _cos_object_exists(cos_object_key: str) -> bool:
    """Thin indirection so tests can monkeypatch without touching cos_client."""
    return _cos_mod.object_exists(cos_object_key)


def _download_cos_to_file(cos_object_key: str, local_path: Path) -> int:
    """Stream COS object to *local_path*; return bytes written."""
    client, bucket = _cos_mod._get_cos_client()
    resp = client.get_object(Bucket=bucket, Key=cos_object_key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    resp["Body"].get_stream_to_file(str(local_path))
    return local_path.stat().st_size


def _preprocessing_local_path(
    pp_job_id: UUID, *, segment_index: int | None = None, audio: bool = False,
) -> Path:
    """Return the path the preprocessing worker cached the artifact at."""
    settings = get_settings()
    root = Path(settings.extraction_artifact_root) / "preprocessing" / str(pp_job_id)
    if audio:
        return root / "audio.wav"
    if segment_index is None:
        raise ValueError("segment_index required when audio=False")
    return root / "segments" / f"seg_{segment_index:04d}.mp4"


def _kb_segment_path(job_dir: Path, segment_index: int) -> Path:
    return job_dir / "segments" / f"seg_{segment_index:04d}.mp4"


# ── Main executor ───────────────────────────────────────────────────────────

async def execute(
    session: AsyncSession,
    job: ExtractionJob,
    step: PipelineStep,
) -> dict[str, Any]:
    """Populate the KB job dir with preprocessed segments + audio.wav."""
    settings = get_settings()
    job_dir = Path(settings.extraction_artifact_root) / str(job.id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "segments").mkdir(exist_ok=True)

    view = await _load_preprocessing_view(session, job.cos_object_key)

    # ── Feature-021: 清洗门 — 过滤 segments 到 effective_decision='accepted' ──
    curation_metadata = await _apply_curation_gate(
        session, view=view, cos_object_key=job.cos_object_key,
    )
    if curation_metadata.get("low_quality_skip"):
        # accepted_duration_ratio == 0 ⇒ 整个作业短路；orchestrator
        # 会把 RuntimeError 转成 ExtractionJob.status='failed'，但
        # error_code 前缀 'LOW_QUALITY_SKIP:' 让运维清楚区分这是业务结果
        # 而非真实失败。FR-009：不调 LLM、不耗 token。
        raise RuntimeError(format_error(
            "LOW_QUALITY_SKIP",
            f"curation marked accepted_duration_ratio=0 for "
            f"cos_object_key={job.cos_object_key!r} "
            f"(curation_job_id={curation_metadata.get('curation_job_id')})",
        ))

    accepted_segments = curation_metadata.pop("_accepted_segments")

    # ── 1. Pre-flight: head_object every artifact so we fail fast. ───────
    for seg in accepted_segments:
        if not await asyncio.to_thread(_cos_object_exists, seg.cos_object_key):
            raise RuntimeError(format_error(
                SEGMENT_MISSING,
                f"segment_index={seg.segment_index} cos_object_key={seg.cos_object_key!r}",
            ))

    if view.has_audio and view.audio:
        audio_key = view.audio.get("cos_object_key")
        if audio_key and not await asyncio.to_thread(
            _cos_object_exists, audio_key,
        ):
            raise RuntimeError(format_error(
                AUDIO_MISSING,
                f"cos_object_key={audio_key!r}",
            ))

    # ── 2. Fetch segments (local cache first) ─────────────────────────────
    local_cache_hits = 0
    cos_downloads = 0
    for seg in accepted_segments:
        target = _kb_segment_path(job_dir, seg.segment_index)
        if target.exists() and target.stat().st_size == seg.size_bytes:
            local_cache_hits += 1
            continue

        pp_local = _preprocessing_local_path(
            view.job_id, segment_index=seg.segment_index,
        )
        if pp_local.exists() and pp_local.stat().st_size == seg.size_bytes:
            # Hard-link if on same filesystem, else copy.
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if target.exists():
                    target.unlink()
                target.hardlink_to(pp_local)
            except (OSError, AttributeError):  # cross-device or old Python
                shutil.copyfile(pp_local, target)
            local_cache_hits += 1
            continue

        await asyncio.to_thread(
            _download_cos_to_file, seg.cos_object_key, target,
        )
        cos_downloads += 1

    # ── 3. Fetch audio.wav ────────────────────────────────────────────────
    audio_downloaded = False
    if view.has_audio and view.audio:
        audio_key = view.audio.get("cos_object_key")
        expected_size = int(view.audio.get("size_bytes") or 0)
        if audio_key:
            target_audio = job_dir / "audio.wav"
            if target_audio.exists() and (
                expected_size == 0 or target_audio.stat().st_size == expected_size
            ):
                local_cache_hits += 1
            else:
                pp_audio = _preprocessing_local_path(view.job_id, audio=True)
                if pp_audio.exists() and (
                    expected_size == 0 or pp_audio.stat().st_size == expected_size
                ):
                    try:
                        if target_audio.exists():
                            target_audio.unlink()
                        target_audio.hardlink_to(pp_audio)
                    except (OSError, AttributeError):
                        shutil.copyfile(pp_audio, target_audio)
                    local_cache_hits += 1
                else:
                    await asyncio.to_thread(
                        _download_cos_to_file, audio_key, target_audio,
                    )
                    cos_downloads += 1
            audio_downloaded = True

    segments_total = len(view.segments)
    segments_processed = len(accepted_segments)

    output_summary = {
        "video_preprocessing_job_id": str(view.job_id),
        # Feature-021 语义说明：
        #   segments_total              = preprocessing 切出的总分段数（不变）
        #   segments_processed          = 经清洗门后实际下载的分段数
        #   segments_skipped_by_curation = total - processed
        #   segments_downloaded         = 实际下载数（与 processed 一致；保留以
        #                                 兼容历史合约测试）
        "segments_total": segments_total,
        "segments_processed": segments_processed,
        "segments_skipped_by_curation": segments_total - segments_processed,
        "segments_downloaded": segments_processed,
        "audio_downloaded": audio_downloaded,
        "local_cache_hits": local_cache_hits,
        "cos_downloads": cos_downloads,
    }
    # 透传清洗元数据（curation_job_id / rubric_version / warning / bypass）
    output_summary.update(curation_metadata)

    return {
        "status": PipelineStepStatus.success,
        "output_summary": output_summary,
        "output_artifact_path": str(job_dir),
    }


# ── Feature-021 helpers ────────────────────────────────────────────────────


async def _apply_curation_gate(
    session: AsyncSession,
    *,
    view,
    cos_object_key: str,
) -> dict[str, Any]:
    """根据清洗门评估结果筛选 ``view.segments`` 并返回元数据.

    返回 dict 含：

    - ``_accepted_segments`` (临时键，调用方 pop)：过滤后的 segment 列表
    - ``curation_job_id`` / ``curation_rubric_version``：审计锚点
    - ``curation_warning``：``"low_quality"`` 或 ``None``
    - ``curation_bypass``：``True`` 当且仅当 bypass 开关启用
    - ``low_quality_skip``：仅在 ``accepted_duration_ratio==0`` 时为 True，
      调用方应把它转换为 ``LOW_QUALITY_SKIP:`` 错误抛出

    bypass 路径：``view.segments`` 整列直通；``curation_bypass=True`` 留痕；
    其它 curation_* 字段为 None；``low_quality_skip`` 永不为 True。
    """
    from src.models.video_curation_segment_result import (
        VideoCurationSegmentResult,
    )
    from src.services.curation.kb_gate import evaluate_curation_gate

    gate = await evaluate_curation_gate(session, cos_object_key=cos_object_key)

    if gate.decision == "bypassed":
        return {
            "_accepted_segments": list(view.segments),
            "curation_job_id": None,
            "curation_rubric_version": None,
            "curation_warning": None,
            "curation_bypass": True,
            "low_quality_skip": False,
        }

    if gate.decision == "required":
        # router 层应已拦截；进到这里说明开发缺陷或并发竞态——
        # fail-fast 让运维知道
        raise RuntimeError(format_error(
            "CURATION_REQUIRED",
            f"no successful video_curation_jobs for cos_object_key={cos_object_key!r}; "
            "submission router gate should have rejected this",
        ))

    # 取该作业 effective_decision='accepted' 的 segment_index 集合
    accepted_idx_rows = (
        await session.execute(
            select(VideoCurationSegmentResult.segment_index).where(
                VideoCurationSegmentResult.job_id == gate.curation_job_id,
                VideoCurationSegmentResult.effective_decision == "accepted",
            )
        )
    ).all()
    accepted_idx_set = {row[0] for row in accepted_idx_rows}

    accepted_segments = [
        seg for seg in view.segments if seg.segment_index in accepted_idx_set
    ]

    if gate.decision == "low_quality_skip":
        return {
            "_accepted_segments": [],
            "curation_job_id": str(gate.curation_job_id),
            "curation_rubric_version": gate.curation_rubric_version,
            "curation_warning": None,
            "curation_bypass": False,
            "low_quality_skip": True,
        }

    return {
        "_accepted_segments": accepted_segments,
        "curation_job_id": str(gate.curation_job_id),
        "curation_rubric_version": gate.curation_rubric_version,
        "curation_warning": (
            "low_quality" if gate.decision == "low_quality_warn" else None
        ),
        "curation_bypass": False,
        "low_quality_skip": False,
    }
