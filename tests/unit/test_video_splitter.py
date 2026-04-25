"""Unit tests for Feature-016 preprocessing.video_splitter.

split() must:
- Stream segments (generator semantics) so the main thread does not block
  on the whole split finishing before the uploader can start.
- Return SegmentInfo with (index, start_ms, end_ms, local_path) per segment.
- For a video with duration D and segment T:
  - produce ceil(D / T) segments
  - sum of (end_ms - start_ms) == D_ms (accumulated error < 1% — SC-005)
  - each segment individually satisfies |duration - T| < 1s (SC-005),
    except the last one which may be shorter.
- For a video with duration ≤ T, yield a single segment covering [0, D_ms].
- Map ffmpeg failure to RuntimeError with ``VIDEO_SPLIT_FAILED:`` prefix.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestVideoSplitterSegmentation:
    def test_600s_video_produces_4_segments_of_180s(self, tmp_path):
        from src.services.preprocessing import video_splitter

        in_path = tmp_path / "in.mp4"
        in_path.write_bytes(b"fake")
        out_dir = tmp_path / "segments"
        out_dir.mkdir()

        # Simulate ffmpeg creating 4 files on disk: 180 / 180 / 180 / 60.
        durations = [180_000, 180_000, 180_000, 60_000]
        files = []
        for i in range(4):
            f = out_dir / f"seg_{i:04d}.mp4"
            f.write_bytes(b"x" * (1024 * (i + 1)))
            files.append(f)

        # Stub ffmpeg subprocess + the probe that reads back each segment's
        # real duration.  The splitter is expected to call ffprobe once per
        # emitted segment to determine its actual end_ms.
        with patch.object(
            video_splitter, "_run_ffmpeg_split", return_value=None,
        ), patch.object(
            video_splitter, "_probe_segment_duration_ms",
            side_effect=durations,
        ):
            segs = list(
                video_splitter.split(
                    input_path=in_path,
                    output_dir=out_dir,
                    total_duration_ms=600_000,
                    segment_duration_s=180,
                )
            )

        assert len(segs) == 4
        # Ordering & timeline coverage
        assert [s.segment_index for s in segs] == [0, 1, 2, 3]
        assert segs[0].start_ms == 0
        assert segs[-1].end_ms == 600_000
        for s, f in zip(segs, files):
            assert s.local_path == f

        # SC-005: each interior segment duration within 1s of 180s
        for s in segs[:-1]:
            assert abs((s.end_ms - s.start_ms) - 180_000) < 1000, s

        # SC-005: cumulative duration matches source within 1%
        total = sum(s.end_ms - s.start_ms for s in segs)
        assert abs(total - 600_000) / 600_000 < 0.01

    def test_short_video_yields_single_segment(self, tmp_path):
        from src.services.preprocessing import video_splitter

        in_path = tmp_path / "in.mp4"
        in_path.write_bytes(b"fake")
        out_dir = tmp_path / "segments"
        out_dir.mkdir()
        single = out_dir / "seg_0000.mp4"
        single.write_bytes(b"data")

        with patch.object(
            video_splitter, "_run_ffmpeg_split", return_value=None,
        ), patch.object(
            video_splitter, "_probe_segment_duration_ms",
            side_effect=[120_000],
        ):
            segs = list(
                video_splitter.split(
                    input_path=in_path,
                    output_dir=out_dir,
                    total_duration_ms=120_000,
                    segment_duration_s=180,
                )
            )

        assert len(segs) == 1
        assert segs[0].segment_index == 0
        assert segs[0].start_ms == 0
        assert segs[0].end_ms == 120_000

    def test_ffmpeg_failure_raises_prefixed_error(self, tmp_path):
        from src.services.preprocessing import video_splitter

        in_path = tmp_path / "in.mp4"
        in_path.write_bytes(b"fake")
        out_dir = tmp_path / "segments"
        out_dir.mkdir()

        with patch.object(
            video_splitter, "_run_ffmpeg_split",
            side_effect=RuntimeError("ffmpeg exit 1"),
        ):
            with pytest.raises(RuntimeError, match=r"^VIDEO_SPLIT_FAILED:"):
                list(video_splitter.split(
                    input_path=in_path,
                    output_dir=out_dir,
                    total_duration_ms=600_000,
                    segment_duration_s=180,
                ))
