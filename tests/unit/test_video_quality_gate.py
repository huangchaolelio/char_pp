"""Unit tests — Feature 015 video quality gate (T007).

Covers FR-006 + spec Q3: pose_analysis fail fast with structured error prefix
when video fails Feature-002 quality thresholds.

The underlying ``validate_video()`` is tested by Feature-002; here we focus
on the *executor-level* contract: how pose_analysis translates
``VideoQualityRejected`` into a ``VIDEO_QUALITY_REJECTED:`` prefix error.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.kb_extraction_pipeline.error_codes import VIDEO_QUALITY_REJECTED


pytestmark = pytest.mark.unit


class TestVideoQualityGateContract:
    """Executor behaviour around ``validate_video``'s reject exception."""

    def test_video_quality_rejected_exception_fields(self) -> None:
        """``VideoQualityRejected`` carries a reason + details dict that
        pose_analysis must translate into a structured error prefix."""
        from src.services.video_validator import VideoQualityRejected

        exc = VideoQualityRejected(
            "fps_below_threshold",
            details={"field": "fps", "actual": 12.0, "threshold": 15.0},
        )
        assert exc.reason == "fps_below_threshold"
        assert exc.details["field"] == "fps"
        assert exc.details["actual"] == 12.0

    def test_format_error_produces_greppable_prefix(self) -> None:
        """Ops scripts must be able to ``grep -c '^VIDEO_QUALITY_REJECTED:'``
        the error_message column without locale-dependent matching."""
        from src.services.kb_extraction_pipeline.error_codes import format_error

        msg = format_error(VIDEO_QUALITY_REJECTED, "fps=12.0 vs 15.0")
        assert msg.startswith("VIDEO_QUALITY_REJECTED: ")
        code, _, details = msg.partition(": ")
        assert code == "VIDEO_QUALITY_REJECTED"
        assert "fps" in details

    @pytest.mark.asyncio
    async def test_pose_analysis_translates_reject_to_prefixed_error(
        self, tmp_path, monkeypatch
    ) -> None:
        """End-to-end: patch video_validator + confirm executor raises
        RuntimeError with the agreed prefix (not the raw VideoQualityRejected)."""
        from types import SimpleNamespace

        from src.services.kb_extraction_pipeline.step_executors import pose_analysis
        from src.services.video_validator import VideoQualityRejected

        # Build a fake download_dir with a seg_0000.mp4 present so pose_analysis
        # can attempt quality-gate on segment 0.
        download_dir = tmp_path / "download"
        (download_dir / "segments").mkdir(parents=True)
        seg0_path = download_dir / "segments" / "seg_0000.mp4"
        seg0_path.write_bytes(b"\x00")

        job = MagicMock()
        job.id = "test-job-quality-gate"
        job.cos_object_key = "tests/quality_gate.mp4"

        step = MagicMock()
        step.output_artifact_path = None

        # Patch _get_video_path to return our fake download_dir (directory).
        async def _patched_resolve_video_path(session, job, step_id=None):
            return str(download_dir)

        # US2: pose_analysis also calls _load_preprocessing_view before validate.
        async def _fake_load_view(session, cos_object_key):
            return SimpleNamespace(
                segments=[
                    SimpleNamespace(segment_index=0, start_ms=0, end_ms=180_000),
                ],
                original_meta=None,
                audio_cos_object_key=None,
            )

        # Patch validate_video to always reject.
        def _fake_validate(path):
            raise VideoQualityRejected(
                "fps_below_threshold",
                details={"field": "fps", "actual": 12.0, "threshold": 15.0},
            )

        from src.services import video_validator as vv_mod
        monkeypatch.setattr(vv_mod, "validate_video", _fake_validate, raising=True)

        monkeypatch.setattr(
            pose_analysis,
            "_get_video_path",
            _patched_resolve_video_path,
            raising=False,
        )
        monkeypatch.setattr(
            pose_analysis,
            "_load_preprocessing_view",
            _fake_load_view,
            raising=False,
        )

        session = MagicMock()
        with pytest.raises(RuntimeError) as excinfo:
            await pose_analysis.execute(session, job, step)
        assert str(excinfo.value).startswith("VIDEO_QUALITY_REJECTED: ")
        # Human-readable detail includes the offending field + value.
        assert "fps" in str(excinfo.value)
