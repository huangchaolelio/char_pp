"""Feature-016 — preprocessing top-level coordinator.

``run_preprocessing(job_id)`` drives the full pipeline for a single
``video_preprocessing_jobs`` row:

    download → probe/validate → persist probe meta
             → transcode (standardise)
             → split (stream)  ──┐
                                 ├─ concurrently upload to COS
             → audio_export  ────┘
             → record_success + mark classification.preprocessed=True

On any failure the job row is transitioned to ``status='failed'`` with a
structured error prefix (SC-007). Local artefacts are kept so KB-extraction
consumption (US2) can reuse them — the 24h TTL sweep in
``cleanup_intermediate_artifacts`` handles disposal.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from uuid import UUID

from src.config import get_settings
from src.db import session as db_session
from src.services import cos_client as cos_client_mod
from src.services import preprocessing_service
from src.services.preprocessing import (
    audio_exporter,
    cos_uploader,
    error_codes,
    video_probe,
    video_splitter,
    video_transcoder,
)


logger = logging.getLogger(__name__)


# ── Path conventions ────────────────────────────────────────────────────────

def _job_local_dir(job_id: UUID) -> Path:
    root = Path(get_settings().extraction_artifact_root) / "preprocessing" / str(job_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _segment_cos_key(cos_object_key: str, job_id: UUID, index: int) -> str:
    return (
        f"preprocessed/{cos_object_key}/jobs/{job_id}/seg_{index:04d}.mp4"
    )


def _audio_cos_key(cos_object_key: str, job_id: UUID) -> str:
    return f"preprocessed/{cos_object_key}/jobs/{job_id}/audio.wav"


# ── Download helper (sync, run in thread) ───────────────────────────────────

def _download_video(cos_object_key: str, local_path: Path) -> int:
    """Synchronously stream a COS object to local disk."""
    client, bucket = cos_client_mod._get_cos_client()
    try:
        response = client.get_object(Bucket=bucket, Key=cos_object_key)
        response["Body"].get_stream_to_file(str(local_path))
    except Exception as exc:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_DOWNLOAD_FAILED,
                f"{cos_object_key}: {exc}",
            )
        ) from exc
    size = local_path.stat().st_size
    if size == 0:
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_DOWNLOAD_FAILED,
                f"{cos_object_key}: empty body",
            )
        )
    return size


# ── Main coordinator ────────────────────────────────────────────────────────

async def run_preprocessing(job_id: UUID) -> None:
    """Execute the full preprocessing pipeline for ``job_id``.

    Any failure is persisted as ``status='failed'`` with an error-prefix
    message. On success the paired ``coach_video_classifications`` row has
    ``preprocessed=true``.
    """
    settings = get_settings()
    local_dir = _job_local_dir(job_id)

    # Load the job row up-front so we have cos_object_key etc.
    async with db_session.AsyncSessionFactory() as s:
        from src.models.video_preprocessing_job import VideoPreprocessingJob
        job = await s.get(VideoPreprocessingJob, job_id)
        if job is None:
            logger.error("run_preprocessing: job %s not found", job_id)
            return
        cos_object_key = job.cos_object_key

    try:
        # ── 1. Download ─────────────────────────────────────────────────────
        suffix = Path(cos_object_key).suffix or ".mp4"
        input_path = local_dir / f"original{suffix}"
        logger.info("preprocessing %s: downloading", job_id)
        await asyncio.to_thread(_download_video, cos_object_key, input_path)

        # ── 2. Probe + validate ─────────────────────────────────────────────
        logger.info("preprocessing %s: probing", job_id)
        meta = await asyncio.to_thread(video_probe.probe_and_validate, input_path)

        target_standard = {
            "target_fps": settings.video_preprocessing_target_fps,
            "target_short_side": settings.video_preprocessing_target_short_side,
            "segment_duration_s": settings.video_preprocessing_segment_duration_s,
        }
        async with db_session.AsyncSessionFactory() as s:
            await preprocessing_service.persist_original_meta(
                s, job_id,
                original_meta=meta.to_json_dict(),
                target_standard=target_standard,
                has_audio=meta.has_audio,
                local_artifact_dir=str(local_dir),
            )
            await s.commit()

        # ── 3. Transcode ────────────────────────────────────────────────────
        standardised_path = local_dir / "standardised.mp4"
        logger.info("preprocessing %s: transcoding → %s", job_id, standardised_path)
        await asyncio.to_thread(
            video_transcoder.transcode,
            input_path, standardised_path,
            target_fps=settings.video_preprocessing_target_fps,
            target_short_side=settings.video_preprocessing_target_short_side,
        )

        # ── 4. Split (streaming) + concurrent upload to COS ─────────────────
        segments_dir = local_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        uploader = cos_uploader.ConcurrentUploader()

        segment_rows: list[dict[str, Any]] = []
        upload_futures: list[tuple[dict[str, Any], Future]] = []

        logger.info("preprocessing %s: splitting + uploading", job_id)

        def _split_iter():
            yield from video_splitter.split(
                input_path=standardised_path,
                output_dir=segments_dir,
                total_duration_ms=meta.duration_ms,
                segment_duration_s=settings.video_preprocessing_segment_duration_s,
            )

        # The split function is sync; drain it in a thread while letting the
        # uploader ThreadPool consume segments in parallel.
        segments = await asyncio.to_thread(lambda: list(_split_iter()))

        for seg in segments:
            cos_key = _segment_cos_key(cos_object_key, job_id, seg.segment_index)
            fut = uploader.submit_segment(seg.local_path, cos_key)
            row = {
                "segment_index": seg.segment_index,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "cos_object_key": cos_key,
                "local_path": seg.local_path,
            }
            segment_rows.append(row)
            upload_futures.append((row, fut))

        # ── 5. Audio export (from ORIGINAL, parallel with uploads) ─────────
        audio_path = local_dir / "audio.wav"
        audio_future_result: tuple[Path | None, int] | None = None
        try:
            audio_future_result = await asyncio.to_thread(
                audio_exporter.export_wav,
                input_path=input_path,
                output_path=audio_path,
                has_audio=meta.has_audio,
            )
        except Exception as audio_exc:
            # Let segment uploads finish first so we don't leave half-done state.
            for _, fut in upload_futures:
                try:
                    fut.result(timeout=3600)
                except Exception:
                    pass
            uploader.shutdown()
            raise audio_exc

        # ── 6. Block on segment uploads ────────────────────────────────────
        for row, fut in upload_futures:
            fut.result(timeout=3600)
            row["size_bytes"] = row["local_path"].stat().st_size

        # ── 7. Upload audio if present ─────────────────────────────────────
        audio_cos_key: str | None = None
        audio_size: int | None = None
        if audio_future_result is not None and audio_future_result[0] is not None:
            audio_local, _size = audio_future_result
            audio_cos_key = _audio_cos_key(cos_object_key, job_id)
            audio_fut = uploader.submit_segment(audio_local, audio_cos_key)
            audio_fut.result(timeout=3600)
            audio_size = audio_local.stat().st_size

        uploader.shutdown()

        # ── 8. Persist segment rows + finalise job ─────────────────────────
        async with db_session.AsyncSessionFactory() as s:
            for row in segment_rows:
                await preprocessing_service.add_segment_row(
                    s,
                    job_id=job_id,
                    segment_index=row["segment_index"],
                    start_ms=row["start_ms"],
                    end_ms=row["end_ms"],
                    cos_object_key=row["cos_object_key"],
                    size_bytes=row["size_bytes"],
                )
            await preprocessing_service.record_job_success(
                s, job_id,
                duration_ms=meta.duration_ms,
                segment_count=len(segment_rows),
                original_meta=meta.to_json_dict(),
                target_standard=target_standard,
                has_audio=bool(audio_cos_key),
                audio_cos_object_key=audio_cos_key,
                audio_size_bytes=audio_size,
                local_artifact_dir=str(local_dir),
            )
            # FR-006: flip the classifications flag
            await preprocessing_service.mark_preprocessed(
                s, cos_object_key=cos_object_key,
            )
            await s.commit()

        # ── 9. Remove the large transcode intermediate; keep segments + WAV
        # as temp cache for US2 reuse (FR-005c).
        try:
            if standardised_path.exists():
                standardised_path.unlink()
            if input_path.exists():
                input_path.unlink()
        except OSError:
            pass

        logger.info(
            "preprocessing %s DONE — %d segments, has_audio=%s",
            job_id, len(segment_rows), bool(audio_cos_key),
        )

    except Exception as exc:
        # All errors must reach DB with structured prefix. If the exception
        # already carries one (via error_codes.format_error) we keep it as-is.
        msg = str(exc)
        if not any(msg.startswith(p + ":") for p in (
            error_codes.VIDEO_DOWNLOAD_FAILED,
            error_codes.VIDEO_PROBE_FAILED,
            error_codes.VIDEO_QUALITY_REJECTED,
            error_codes.VIDEO_CODEC_UNSUPPORTED,
            error_codes.VIDEO_TRANSCODE_FAILED,
            error_codes.VIDEO_SPLIT_FAILED,
            error_codes.VIDEO_UPLOAD_FAILED,
            error_codes.AUDIO_EXTRACT_FAILED,
        )):
            msg = error_codes.format_error(
                error_codes.VIDEO_TRANSCODE_FAILED, f"unexpected: {exc}"
            )
        logger.exception("preprocessing %s FAILED — %s", job_id, msg)
        try:
            async with db_session.AsyncSessionFactory() as s:
                await preprocessing_service.record_job_failed(s, job_id, msg)
                await s.commit()
        except Exception:
            logger.exception("also failed to persist failure state for %s", job_id)
