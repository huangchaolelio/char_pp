"""Celery task: process a professional coach video to build a knowledge base draft.

Flow (T024):
  1. Mark task as processing
  2. Verify COS object exists -> fail with COS_OBJECT_NOT_FOUND if missing
  3. Download to local temp dir via cos_client
  4. Validate video quality -> reject if below threshold
  5. Run MediaPipe pose estimation on every frame
  6. Segment frames into discrete action clips (wrist-velocity peaks)
  7. Classify each segment (forehand_topspin / backhand_push / unknown)
  8. Extract technical dimensions per classified segment
  8.5. Audio-enhanced extraction (Feature 002): Whisper + KB merge
  9. Persist ExpertTechPoints + create draft TechKnowledgeBase version in one DB transaction
  10. Clean up local temp file
  11. AnalysisTask.status = success (set inside the same transaction as step 9)

Error handling:
  - VideoQualityRejected      -> status=rejected, rejection_reason set, temp cleaned
  - CosObjectNotFoundError    -> status=failed,   error_message=COS_OBJECT_NOT_FOUND
  - CosDownloadError          -> status=failed,   error_message=COS_DOWNLOAD_FAILED
  - AudioExtractionError      -> fallback_reason=AUDIO_EXTRACTION_FAILED (non-fatal, visual-only)
  - Unsupported audio language -> fallback_reason=UNSUPPORTED_AUDIO_LANGUAGE (non-fatal)
  - ConflictUnresolvedError   -> blocks KB approval via API (409 CONFLICT_UNRESOLVED)
  - Any other exception       -> status=failed,   error_message set, task retried (max 2x)
"""

from __future__ import annotations


import asyncio
import math
import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from celery import shared_task

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus
from src.models.audio_transcript import AudioQualityFlag, AudioTranscript
from src.models.tech_semantic_segment import TechSemanticSegment
from src.services import (
    action_classifier,
    action_segmenter,
    cos_client,
    knowledge_base_svc,
    pose_estimator,
    tech_extractor,
    video_validator,
)
from src.services.audio_extractor import AudioExtractionError, AudioExtractor
from src.services.cos_client import CosDownloadError, CosObjectNotFoundError
from src.services.kb_merger import KbMerger
from src.services.keyword_locator import KeywordLocator, PriorityWindow
from src.services.speech_recognizer import SpeechRecognizer
from src.services.transcript_tech_parser import TranscriptTechParser
from src.services.subtitle_validator import SubtitleValidator
from src.services.video_validator import VideoMeta, VideoQualityRejected

logger = logging.getLogger(__name__)


def _make_session_factory():
    """Create a fresh async engine + sessionmaker for each Celery task invocation.

    Celery forks new processes, so we must NOT reuse the module-level engine
    (which is bound to the parent's event loop). Creating a fresh engine here
    ensures asyncpg connects on the current event loop.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    settings = get_settings()
    _engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _set_processing(task_id: uuid.UUID) -> None:
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.status = TaskStatus.processing
                task.started_at = datetime.now(tz=timezone.utc)


async def _set_rejected(task_id: uuid.UUID, reason: str) -> None:
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.status = TaskStatus.rejected
                task.rejection_reason = reason
                task.completed_at = datetime.now(tz=timezone.utc)


async def _set_failed(task_id: uuid.UUID, error_message: str) -> None:
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.status = TaskStatus.failed
                task.error_message = error_message
                task.completed_at = datetime.now(tz=timezone.utc)


async def _persist_success(
    task_id: uuid.UUID,
    video_meta: VideoMeta,
    extraction_results: list,
    audio_fallback_reason: str | None = None,
) -> str:
    """Create draft KB, save tech points, and mark task success — all in one transaction.

    Returns the newly created KB version string.
    """
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            # Derive action types covered from extracted results (fall back to v1 defaults)
            action_types = list({r.action_type for r in extraction_results}) or [
                "forehand_topspin",
                "backhand_push",
            ]

            kb = await knowledge_base_svc.create_draft_version(
                session,
                action_types=action_types,
                notes=f"Auto-extracted from task {task_id}",
            )
            kb_version = kb.version

            if extraction_results:
                count = await knowledge_base_svc.add_tech_points(
                    session,
                    kb_version=kb_version,
                    source_task_id=task_id,
                    extraction_results=extraction_results,
                )
                logger.info(
                    "Saved %d tech points to KB draft %s (task %s)",
                    count, kb_version, task_id,
                )

            # Update AnalysisTask fields in the same transaction
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.status = TaskStatus.success
                task.completed_at = datetime.now(tz=timezone.utc)
                task.video_fps = video_meta.fps
                task.video_resolution = video_meta.resolution_str
                task.video_duration_seconds = video_meta.duration_seconds
                task.knowledge_base_version = kb_version
                task.audio_fallback_reason = audio_fallback_reason

    return kb_version


async def _persist_audio_transcript(
    task_id: uuid.UUID,
    transcript_result,
    segments: list[TechSemanticSegment],
) -> AudioTranscript:
    """Persist AudioTranscript and its TechSemanticSegments to the DB."""
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            at = AudioTranscript(
                task_id=task_id,
                language=transcript_result.language,
                model_version=transcript_result.model_version,
                total_duration_s=transcript_result.total_duration_s,
                snr_db=transcript_result.snr_db,
                quality_flag=transcript_result.quality_flag,
                fallback_reason=transcript_result.fallback_reason,
                sentences=transcript_result.sentences,
            )
            session.add(at)
            await session.flush()  # get at.id

            for seg in segments:
                seg.transcript_id = at.id
                seg.task_id = task_id
                session.add(seg)

    return at


# ── Audio pipeline helper ───────────────────────────────────────────────────────

def _run_audio_pipeline(
    video_path: Path,
    task_id: uuid.UUID,
    enable_audio: bool = True,
    language: str = "zh",
) -> tuple[list, list[PriorityWindow], str | None]:
    """Run audio extraction + recognition + parsing + keyword localisation pipeline.

    Also performs subtitle sync validation (T038): if the video has embedded
    subtitles, their timestamps are cross-validated against the Whisper transcript.
    A desync > 2s is recorded as a suffix in the returned fallback_reason.

    Returns:
        (audio_segments, priority_windows, fallback_reason) where:
        - audio_segments: TechSemanticSegment list (empty on fallback)
        - priority_windows: PriorityWindow list for segment prioritisation (empty on fallback)
        - fallback_reason: None on success, structured error code string on fallback

    Fallback reason codes (audio):
        - "audio_analysis_disabled"       : enable_audio=False
        - "AUDIO_EXTRACTION_FAILED: ..."  : ffmpeg failed
        - "low_snr: X dB (threshold: Y dB)": SNR below threshold
        - "UNSUPPORTED_AUDIO_LANGUAGE: ...": language not in SUPPORTED_LANGUAGES
        - "AUDIO_QUALITY_INSUFFICIENT: ...": other quality flag (silent, etc.)
    Appended subtitle suffixes (separated by "; "):
        - "subtitle_out_of_sync: X.Xs"   : subtitle timestamps desynced
        - "subtitle_unsupported_format: not_srt": embedded subtitles not SRT
    """
    if not enable_audio:
        return [], [], "audio_analysis_disabled"

    settings = get_settings()
    wav_path = video_path.parent / f"audio_{video_path.stem}.wav"
    srt_path = video_path.parent / f"subtitle_{video_path.stem}.srt"

    try:
        extractor = AudioExtractor(snr_threshold_db=settings.audio_snr_threshold_db)
        try:
            extractor.extract_wav(video_path, wav_path)
        except AudioExtractionError as exc:
            logger.warning(
                "[AUDIO_EXTRACTION_FAILED] task %s: %s", task_id, exc
            )
            return [], [], f"AUDIO_EXTRACTION_FAILED: {exc}"

        # SNR quality check
        is_sufficient, snr_db = extractor.is_quality_sufficient(wav_path)
        if not is_sufficient:
            logger.info(
                "Audio SNR %.1f dB below threshold %.1f dB for task %s — falling back",
                snr_db, settings.audio_snr_threshold_db, task_id,
            )
            return [], [], f"low_snr: {snr_db:.1f} dB (threshold: {settings.audio_snr_threshold_db} dB)"

        recognizer = SpeechRecognizer(
            model_name=settings.whisper_model,
            device=settings.whisper_device,
        )
        transcript_result = recognizer.recognize(str(wav_path), language=language)
        transcript_result.snr_db = snr_db

        # Persist transcript + segments
        if transcript_result.quality_flag != AudioQualityFlag.ok:
            flag_val = transcript_result.quality_flag.value if transcript_result.quality_flag else "unknown"
            if transcript_result.quality_flag == AudioQualityFlag.unsupported_language:
                error_code = "UNSUPPORTED_AUDIO_LANGUAGE"
            else:
                error_code = "AUDIO_QUALITY_INSUFFICIENT"
            fallback_msg = f"{error_code}: {flag_val}"
            logger.info(
                "[%s] task %s — %s",
                error_code, task_id, transcript_result.fallback_reason,
            )
            asyncio.run(_persist_audio_transcript(task_id, transcript_result, []))
            return [], [], fallback_msg

        parser = TranscriptTechParser()
        segments = parser.parse(transcript_result.sentences)
        asyncio.run(_persist_audio_transcript(task_id, transcript_result, segments))

        kb_segments = [s for s in segments if not s.is_reference_note]

        # US2: locate priority windows from keyword hits in transcript
        priority_windows: list[PriorityWindow] = []
        try:
            kw_locator = KeywordLocator(keyword_file_path=settings.audio_keyword_file)
            video_duration_ms = int(
                (transcript_result.total_duration_s or 0) * 1000
            ) or 90 * 60 * 1000  # fallback 90min if duration unknown
            priority_windows = kw_locator.locate(
                transcript_result.sentences,
                video_duration_ms=video_duration_ms,
                window_s=settings.audio_priority_window_s,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("KeywordLocator failed for task %s (non-fatal): %s", task_id, exc)

        # T038: Subtitle sync validation — cross-validate embedded subtitles vs transcript
        subtitle_suffix: str | None = None
        try:
            sub_validator = SubtitleValidator()
            extracted = SubtitleValidator.extract_embedded_srt(video_path, srt_path)
            if extracted:
                srt_sentences = SubtitleValidator.parse_srt(srt_path)
                if not srt_sentences:
                    # File extracted but no valid SRT timecodes → unsupported format
                    subtitle_suffix = SubtitleValidator.unsupported_format_suffix()
                    logger.info(
                        "Subtitle format not supported for task %s — %s",
                        task_id, subtitle_suffix,
                    )
                else:
                    sync_result = sub_validator.validate(
                        transcript_result.sentences, srt_sentences
                    )
                    if not sync_result.is_valid:
                        subtitle_suffix = sync_result.fallback_suffix
                        logger.info(
                            "Subtitle out of sync for task %s — %s",
                            task_id, subtitle_suffix,
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Subtitle validation failed for task %s (non-fatal): %s", task_id, exc)

        logger.info(
            "Audio pipeline: %d sentences → %d tech segments, %d priority windows (task %s)",
            len(transcript_result.sentences), len(kb_segments), len(priority_windows), task_id,
        )

        # Combine audio reason (None = ok) with optional subtitle suffix
        audio_reason = None  # success path: no audio fallback
        final_reason = "; ".join(filter(None, [audio_reason, subtitle_suffix])) or None
        return kb_segments, priority_windows, final_reason

    finally:
        # Always clean up WAV and SRT temp files (data privacy)
        if wav_path.exists():
            try:
                wav_path.unlink()
                logger.debug("WAV temp file deleted: %s", wav_path)
            except Exception:  # noqa: BLE001
                logger.debug("WAV cleanup failed (non-fatal): %s", wav_path)
        if srt_path.exists():
            try:
                srt_path.unlink()
                logger.debug("SRT temp file deleted: %s", srt_path)
            except Exception:  # noqa: BLE001
                logger.debug("SRT cleanup failed (non-fatal): %s", srt_path)



def _get_video_duration_ffprobe(video_path: Path) -> float | None:
    """Use ffprobe to get video duration in seconds. Returns None on failure."""
    ffprobe_bin = shutil.which("ffprobe") or "/opt/conda/bin/ffprobe"
    try:
        result = subprocess.run(
            [
                ffprobe_bin, "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return None


async def _update_progress(
    task_id: uuid.UUID,
    processed: int,
    total: int,
) -> None:
    """Persist progress fields to DB (non-transactional, best-effort)."""
    pct = round(min(processed / total * 100, 100.0), 2) if total > 0 else 0.0
    factory = _make_session_factory()
    async with factory() as session:
        async with session.begin():
            task = await session.get(AnalysisTask, task_id)
            if task:
                task.processed_segments = processed
                task.progress_pct = pct


def _cleanup(tmp_path: Path | None) -> None:
    """Best-effort temp file cleanup — never raises."""
    if tmp_path is not None:
        try:
            cos_client.cleanup_temp_file(tmp_path)
        except Exception:  # noqa: BLE001
            logger.debug("Temp file cleanup skipped (non-fatal): %s", tmp_path)


# ── Celery task ────────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name="src.workers.expert_video_task.process_expert_video",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def process_expert_video(
    self,
    task_id_str: str,
    cos_object_key: str,
    enable_audio_analysis: bool = True,
    audio_language: str = "zh",
) -> dict:
    """Process a professional coach video and create a draft knowledge base version.

    Args:
        task_id_str: String representation of the AnalysisTask UUID.
        cos_object_key: COS object key for the video file.
        enable_audio_analysis: Whether to run Whisper audio extraction (Feature 002).
        audio_language: Language code for Whisper recognition (default: 'zh').

    Returns:
        Dict with task_id, status, and on success: kb_version_draft, extracted_segments.
    """
    task_id = uuid.UUID(task_id_str)
    tmp_path: Path | None = None

    logger.info("expert_video task started | task_id=%s cos_key=%s", task_id, cos_object_key)

    try:
        # ── 1. Mark as processing ──────────────────────────────────────────────
        asyncio.run(_set_processing(task_id))

        # ── 2. Verify COS object exists ────────────────────────────────────────
        if not cos_client.object_exists(cos_object_key):
            logger.warning("COS object missing: %s (task %s)", cos_object_key, task_id)
            asyncio.run(_set_failed(task_id, "COS_OBJECT_NOT_FOUND"))
            return {"task_id": task_id_str, "status": "failed", "error": "COS_OBJECT_NOT_FOUND"}

        # ── 3. Download to temp dir ────────────────────────────────────────────
        try:
            tmp_path = cos_client.download_to_temp(cos_object_key)
        except CosObjectNotFoundError:
            asyncio.run(_set_failed(task_id, "COS_OBJECT_NOT_FOUND"))
            return {"task_id": task_id_str, "status": "failed", "error": "COS_OBJECT_NOT_FOUND"}
        except CosDownloadError as exc:
            asyncio.run(_set_failed(task_id, f"COS_DOWNLOAD_FAILED: {exc.reason}"))
            return {"task_id": task_id_str, "status": "failed", "error": "COS_DOWNLOAD_FAILED"}

        # ── 4. Validate video quality ──────────────────────────────────────────
        try:
            video_meta = video_validator.validate_video(tmp_path)
            logger.info(
                "Video validated: fps=%.1f res=%s dur=%.1fs (task %s)",
                video_meta.fps, video_meta.resolution_str, video_meta.duration_seconds, task_id,
            )
        except VideoQualityRejected as exc:
            _cleanup(tmp_path)
            tmp_path = None
            asyncio.run(_set_rejected(task_id, f"{exc.reason}: {exc.details}"))
            return {
                "task_id": task_id_str,
                "status": "rejected",
                "reason": exc.reason,
                "details": exc.details,
            }

        # ── 4.5 ffprobe duration check (US3 — post-download guarantee) ─────────
        settings = get_settings()
        detected_duration = _get_video_duration_ffprobe(tmp_path) or video_meta.duration_seconds
        if detected_duration and detected_duration > settings.max_video_duration_s:
            _cleanup(tmp_path)
            tmp_path = None
            reason = (
                f"VIDEO_TOO_LONG: {detected_duration:.0f}s > "
                f"{settings.max_video_duration_s}s limit"
            )
            asyncio.run(_set_rejected(task_id, reason))
            logger.warning("[VIDEO_TOO_LONG] task %s: %.0fs", task_id, detected_duration)
            return {"task_id": task_id_str, "status": "rejected", "reason": reason}

        # ── 5. Calculate segments and persist total_segments (US3) ────────────
        segment_duration_s = settings.long_video_segment_duration_s
        total_duration_s = detected_duration or segment_duration_s
        total_segments = max(1, math.ceil(total_duration_s / segment_duration_s))
        factory = _make_session_factory()
        async def _set_total_segments() -> None:
            async with factory() as session:
                async with session.begin():
                    task_obj = await session.get(AnalysisTask, task_id)
                    if task_obj:
                        task_obj.total_segments = total_segments
                        task_obj.processed_segments = 0
                        task_obj.progress_pct = 0.0
        asyncio.run(_set_total_segments())
        logger.info(
            "Long-video plan: %.0fs → %d segments of %ds each (task %s)",
            total_duration_s, total_segments, segment_duration_s, task_id,
        )

        # ── 6–8. Segmented processing loop (US3) ──────────────────────────────
        all_extraction_results: list = []
        failed_segment_indices: list[int] = []

        for seg_idx in range(total_segments):
            seg_start_s = seg_idx * segment_duration_s
            logger.info(
                "Processing segment %d/%d (start=%.0fs, task %s)",
                seg_idx + 1, total_segments, seg_start_s, task_id,
            )
            try:
                # Clip segment
                seg_path = tmp_path.parent / f"seg_{seg_idx:04d}_{tmp_path.stem}.mp4"
                ffmpeg_bin = shutil.which("ffmpeg") or "/opt/conda/bin/ffmpeg"
                clip_cmd = [
                    ffmpeg_bin, "-y",
                    "-ss", str(seg_start_s), "-t", str(segment_duration_s),
                    "-i", str(tmp_path),
                    "-vf", "scale=1280:720",
                    "-c:v", "libx264", "-crf", "23", "-an",
                    str(seg_path),
                ]
                clip_result = subprocess.run(clip_cmd, capture_output=True, timeout=120)
                if clip_result.returncode != 0 or not seg_path.exists():
                    logger.warning(
                        "ffmpeg clip failed for segment %d (task %s)", seg_idx, task_id
                    )
                    failed_segment_indices.append(seg_idx)
                    asyncio.run(_update_progress(task_id, seg_idx + 1, total_segments))
                    continue

                # Pose estimation on this segment
                seg_frames = pose_estimator.estimate_pose(seg_path)
                if not seg_frames:
                    logger.info(
                        "No motion in segment %d/%d (task %s)", seg_idx + 1, total_segments, task_id
                    )
                    _cleanup(seg_path)
                    asyncio.run(_update_progress(task_id, seg_idx + 1, total_segments))
                    continue

                # Segment + classify
                action_segs = action_segmenter.segment_actions(seg_frames)
                classified_segs = []
                for a_seg in action_segs:
                    a_frames = action_segmenter.frames_for_segment(seg_frames, a_seg)
                    classified_segs.append(action_classifier.classify_segment(a_frames, a_seg))

                known_segs = [cs for cs in classified_segs if cs.action_type != "unknown"]

                # Extract tech dimensions
                for cs in known_segs:
                    res = tech_extractor.extract_tech_points(cs, seg_frames)
                    if res.dimensions:
                        all_extraction_results.append(res)

                _cleanup(seg_path)

            except Exception as seg_exc:  # noqa: BLE001
                logger.error(
                    "Segment %d failed (task %s): %s", seg_idx, task_id, seg_exc
                )
                failed_segment_indices.append(seg_idx)
                if seg_path.exists():
                    _cleanup(seg_path)

            asyncio.run(_update_progress(task_id, seg_idx + 1, total_segments))

        logger.info(
            "Segmented processing done: %d segments, %d failed, %d extraction results (task %s)",
            total_segments, len(failed_segment_indices), len(all_extraction_results), task_id,
        )
        extraction_results = all_extraction_results

        # ── 8.5 Audio-enhanced extraction (Feature 002) — runs on full video ──
        audio_segments, priority_windows, audio_fallback_reason = _run_audio_pipeline(
            tmp_path, task_id, enable_audio=enable_audio_analysis, language=audio_language
        )
        if audio_fallback_reason:
            logger.info(
                "Audio pipeline fallback for task %s: %s", task_id, audio_fallback_reason
            )

        # US2: prioritise classified segments that fall inside keyword windows
        # (applied retrospectively to inform log/metrics; extraction already done above)
        if priority_windows:
            logger.info(
                "%d keyword priority windows identified (task %s)", len(priority_windows), task_id
            )

        # Merge visual and audio tech points
        visual_dicts = [
            {
                "dimension": dim.name,
                "param_min": dim.param_min,
                "param_max": dim.param_max,
                "param_ideal": dim.param_ideal,
                "unit": dim.unit,
                "extraction_confidence": dim.confidence,
                "action_type": res.action_type,
            }
            for res in extraction_results
            for dim in res.dimensions
        ]
        merger = KbMerger(conflict_threshold_pct=settings.audio_conflict_threshold_pct)
        merged_points = merger.merge(visual_dicts, audio_segments)
        logger.info(
            "Merged: %d visual dims + %d audio segs → %d merged (%d conflicts) (task %s)",
            len(visual_dicts), len(audio_segments), len(merged_points),
            sum(1 for p in merged_points if p.conflict_flag), task_id,
        )

        # ── 9. Persist to DB ───────────────────────────────────────────────────
        # Determine final status: partial_success if any segment failed
        final_status = (
            TaskStatus.partial_success if failed_segment_indices else TaskStatus.success
        )
        kb_version = asyncio.run(
            _persist_success(task_id, video_meta, merged_points, audio_fallback_reason)
        )
        # Patch status to partial_success if needed (persist_success sets success by default)
        if final_status == TaskStatus.partial_success:
            async def _set_partial(vid: uuid.UUID, failed: list) -> None:
                async with factory() as session:
                    async with session.begin():
                        task_obj = await session.get(AnalysisTask, vid)
                        if task_obj:
                            task_obj.status = TaskStatus.partial_success
                            import json as _json
                            task_obj.error_message = _json.dumps(
                                {"failed_segments": failed}
                            )
            asyncio.run(_set_partial(task_id, failed_segment_indices))

        # ── 10. Clean up temp file ─────────────────────────────────────────────
        _cleanup(tmp_path)
        tmp_path = None

        logger.info(
            "expert_video task DONE | task_id=%s KB_draft=%s status=%s",
            task_id, kb_version, final_status.value,
        )
        return {
            "task_id": task_id_str,
            "status": final_status.value,
            "kb_version_draft": kb_version,
            "extracted_segments": len(merged_points),
            "audio_fallback_reason": audio_fallback_reason,
            "failed_segments": failed_segment_indices,
        }

    except Exception as exc:
        _cleanup(tmp_path)
        tmp_path = None
        logger.exception("Unhandled error in expert_video task %s", task_id)
        try:
            asyncio.run(_set_failed(
                task_id, f"INTERNAL_ERROR: {type(exc).__name__}: {exc}"
            ))
        except Exception:  # noqa: BLE001
            pass
        raise self.retry(exc=exc)
