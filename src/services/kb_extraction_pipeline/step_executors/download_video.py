"""download_video executor (Feature 014, Feature-016 US2 rewrite).

Old behavior (pre-US2): downloaded the full ``video.mp4`` from COS into the job
directory. Whole-video pose / Whisper inference then ran on a single 10-min
file — ran into OOM on the 64 GB pod.

New behavior (US2): consumes the output of a successful ``video_preprocessing``
job:
  - Loads the preprocessing job + its segments via ``preprocessing_service``;
  - head_object-checks every segment + audio.wav (fail-fast with
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
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

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

    # ── 1. Pre-flight: head_object every artifact so we fail fast. ───────
    for seg in view.segments:
        if not await asyncio.to_thread(_cos_object_exists, seg.cos_object_key):
            raise RuntimeError(format_error(
                SEGMENT_MISSING,
                f"segment_index={seg.segment_index} cos_object_key={seg.cos_object_key!r}",
            ))

    if view.has_audio and view.audio_cos_object_key:
        if not await asyncio.to_thread(
            _cos_object_exists, view.audio_cos_object_key,
        ):
            raise RuntimeError(format_error(
                AUDIO_MISSING,
                f"cos_object_key={view.audio_cos_object_key!r}",
            ))

    # ── 2. Fetch segments (local cache first) ─────────────────────────────
    local_cache_hits = 0
    cos_downloads = 0
    for seg in view.segments:
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
    if view.has_audio and view.audio_cos_object_key:
        target_audio = job_dir / "audio.wav"
        expected_size = view.audio_size_bytes or 0
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
                    _download_cos_to_file,
                    view.audio_cos_object_key, target_audio,
                )
                cos_downloads += 1
        audio_downloaded = True

    segments_total = len(view.segments)

    return {
        "status": PipelineStepStatus.success,
        "output_summary": {
            "video_preprocessing_job_id": str(view.job_id),
            "segments_total": segments_total,
            "segments_downloaded": segments_total,
            "audio_downloaded": audio_downloaded,
            "local_cache_hits": local_cache_hits,
            "cos_downloads": cos_downloads,
        },
        "output_artifact_path": str(job_dir),
    }
