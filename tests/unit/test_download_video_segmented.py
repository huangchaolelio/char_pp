"""Feature-016 US2 / T030 — download_video reads preprocessing segments + audio.

Asserts the post-US2 executor:
- Loads the success preprocessing job for the ExtractionJob's cos_object_key;
- head_object-checks every segment + audio.wav in COS;
- Missing segment → ``SEGMENT_MISSING:`` RuntimeError;
- Missing audio.wav (when has_audio=true) → ``AUDIO_MISSING:`` RuntimeError;
- Local-cache-hit: file exists with matching size → skip COS download;
- output_artifact_path → download_dir path (NOT video.mp4);
- output_summary fields: segments_downloaded, segments_total,
  audio_downloaded, local_cache_hits, cos_downloads, video_preprocessing_job_id.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.kb_extraction_pipeline.step_executors import download_video


pytestmark = pytest.mark.asyncio


def _seg(index: int, start_ms: int, size: int, key: str):
    return SimpleNamespace(
        segment_index=index, start_ms=start_ms, end_ms=start_ms + 180_000,
        cos_object_key=key, size_bytes=size,
    )


async def test_download_video_raises_segment_missing(tmp_path, monkeypatch):
    """One segment returns head_object=False → raises SEGMENT_MISSING."""
    job_id = uuid4()

    async def fake_view(session, cos_object_key):
        return SimpleNamespace(
            job_id=uuid4(),
            has_audio=True,
            audio_cos_object_key="preprocessed/x/audio.wav",
            audio_size_bytes=1000,
            segments=[
                _seg(0, 0, 100, "preprocessed/x/seg_0000.mp4"),
                _seg(1, 180_000, 100, "preprocessed/x/seg_0001.mp4"),
            ],
        )
    monkeypatch.setattr(
        download_video, "_load_preprocessing_view", fake_view, raising=False,
    )

    # head_object: seg_0000 exists, seg_0001 missing.
    def fake_exists(key):
        return "seg_0000" in key or "audio.wav" in key
    monkeypatch.setattr(
        download_video, "_cos_object_exists", fake_exists, raising=False,
    )

    from src.config import get_settings
    monkeypatch.setattr(
        get_settings(), "extraction_artifact_root", str(tmp_path / "root"),
        raising=False,
    )

    job = SimpleNamespace(id=job_id, cos_object_key="x/y/z.mp4")
    step = SimpleNamespace(id=uuid4(), output_artifact_path=None)
    session = SimpleNamespace()

    with pytest.raises(RuntimeError) as excinfo:
        await download_video.execute(session, job, step)
    assert "SEGMENT_MISSING" in str(excinfo.value)


async def test_download_video_raises_audio_missing(tmp_path, monkeypatch):
    """has_audio=True but audio.wav missing → AUDIO_MISSING."""
    async def fake_view(session, cos_object_key):
        return SimpleNamespace(
            job_id=uuid4(),
            has_audio=True,
            audio_cos_object_key="preprocessed/x/audio.wav",
            audio_size_bytes=1000,
            segments=[_seg(0, 0, 100, "preprocessed/x/seg_0000.mp4")],
        )
    monkeypatch.setattr(
        download_video, "_load_preprocessing_view", fake_view, raising=False,
    )

    def fake_exists(key):
        # Segments yes, audio no.
        return "seg_" in key
    monkeypatch.setattr(
        download_video, "_cos_object_exists", fake_exists, raising=False,
    )

    from src.config import get_settings
    monkeypatch.setattr(
        get_settings(), "extraction_artifact_root", str(tmp_path / "root"),
        raising=False,
    )

    job = SimpleNamespace(id=uuid4(), cos_object_key="x/y/z.mp4")
    step = SimpleNamespace(id=uuid4(), output_artifact_path=None)
    session = SimpleNamespace()

    with pytest.raises(RuntimeError) as excinfo:
        await download_video.execute(session, job, step)
    assert "AUDIO_MISSING" in str(excinfo.value)


async def test_download_video_local_cache_hit(tmp_path, monkeypatch):
    """All segments + audio already local with matching sizes → 0 COS downloads."""
    pp_job_id = uuid4()

    # Pre-populate the preprocessing LOCAL artifact cache as if preprocessing
    # worker kept the files.
    pp_dir = tmp_path / "root" / "preprocessing" / str(pp_job_id)
    (pp_dir / "segments").mkdir(parents=True)
    for i in range(2):
        (pp_dir / "segments" / f"seg_{i:04d}.mp4").write_bytes(b"X" * 100)
    (pp_dir / "audio.wav").write_bytes(b"A" * 1000)

    async def fake_view(session, cos_object_key):
        return SimpleNamespace(
            job_id=pp_job_id,
            has_audio=True,
            audio_cos_object_key="preprocessed/x/audio.wav",
            audio_size_bytes=1000,
            segments=[
                _seg(0, 0, 100, "preprocessed/x/seg_0000.mp4"),
                _seg(1, 180_000, 100, "preprocessed/x/seg_0001.mp4"),
            ],
        )
    monkeypatch.setattr(
        download_video, "_load_preprocessing_view", fake_view, raising=False,
    )
    monkeypatch.setattr(
        download_video, "_cos_object_exists", lambda k: True, raising=False,
    )

    downloads: list[str] = []

    def fake_download(key, local_path):
        downloads.append(key)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(b"Y" * 100)
        return 100

    monkeypatch.setattr(
        download_video, "_download_cos_to_file", fake_download, raising=False,
    )

    from src.config import get_settings
    monkeypatch.setattr(
        get_settings(), "extraction_artifact_root", str(tmp_path / "root"),
        raising=False,
    )

    job_id = uuid4()
    job = SimpleNamespace(id=job_id, cos_object_key="x/y/z.mp4")
    step = SimpleNamespace(id=uuid4(), output_artifact_path=None)
    session = SimpleNamespace()

    result = await download_video.execute(session, job, step)

    summary = result["output_summary"]
    assert summary["segments_total"] == 2
    assert summary["segments_downloaded"] == 2
    assert summary["audio_downloaded"] is True
    assert summary["local_cache_hits"] == 3  # 2 segs + 1 audio
    assert summary["cos_downloads"] == 0
    assert str(summary["video_preprocessing_job_id"]) == str(pp_job_id)
    # No real COS downloads when cache hits.
    assert downloads == []
    # output_artifact_path → download_dir (not a single file).
    out = Path(result["output_artifact_path"])
    assert out.is_dir()
    assert (out / "segments" / "seg_0000.mp4").exists()
    assert (out / "audio.wav").exists()
