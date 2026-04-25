"""Feature-016 video preprocessing pipeline.

Sub-modules (introduced per tasks.md):

- ``video_probe``          — ffprobe + validate_video gate
- ``video_transcoder``     — standardisation (target fps / short side)
- ``video_splitter``       — streaming split into N × 180s segments
- ``audio_exporter``       — single 16 kHz mono WAV extraction
- ``cos_uploader``         — ThreadPool concurrent upload (max_workers=2)
- ``orchestrator``         — top-level run_preprocessing(job_id) coordinator
- ``error_codes``          — structured error prefixes for grep-based triage
"""
