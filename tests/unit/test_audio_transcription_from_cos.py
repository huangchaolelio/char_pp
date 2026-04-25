"""Feature-016 US2 / T029 — audio_transcription reads the preprocessing audio.wav.

Asserts the post-US2 executor:
- Resolves the audio.wav from ``download_video`` output_dir (already downloaded);
- Forces ``whisper_device='cpu'`` regardless of settings (pod OOM guard);
- Reports ``audio_source='cos_preprocessed'`` + ``whisper_device='cpu'`` in
  output_summary.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.kb_extraction_pipeline.step_executors import audio_transcription


pytestmark = pytest.mark.asyncio


class _FakeTranscriptResult:
    def __init__(self):
        self.language = "zh"
        self.sentences = [{"text": "测试文本"}]
        self.snr_db = 30.0
        self.model_version = "whisper-small"
        self.total_duration_s = 12.5
        self.fallback_reason = None
        # Use a class mimic — AudioQualityFlag.silent has .value='silent'.
        from src.models.audio_transcript import AudioQualityFlag
        self.quality_flag = AudioQualityFlag.ok


class _FakeRecognizer:
    def __init__(self, *, model_name, device):
        # Record for assertion.
        self.model_name = model_name
        self.device = device
        _FakeRecognizer.last_instance = self

    def recognize(self, audio_path, language):
        return _FakeTranscriptResult()


async def test_audio_transcription_forces_cpu_and_uses_existing_wav(
    tmp_path, monkeypatch,
):
    job_id = uuid4()
    download_dir = tmp_path / str(job_id)
    download_dir.mkdir()
    audio_wav = download_dir / "audio.wav"
    audio_wav.write_bytes(b"RIFF....WAVE....")

    # Monkeypatch _get_download_dir → returns the download_dir.
    async def fake_get_download_dir(session, job):
        return download_dir

    monkeypatch.setattr(
        audio_transcription, "_get_download_dir", fake_get_download_dir,
        raising=False,
    )

    # Whisper: inject FakeRecognizer; whisper_device from settings is ignored —
    # executor must force cpu.
    monkeypatch.setattr(
        audio_transcription._speech_mod, "SpeechRecognizer", _FakeRecognizer,
    )
    # settings.whisper_device = 'cuda' — executor must still use 'cpu'.
    from src.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "whisper_device", "cuda", raising=False)

    # Bypass SNR estimation (needs real wav).
    monkeypatch.setattr(
        audio_transcription, "_estimate_snr_if_possible",
        lambda p: 25.0, raising=False,
    )

    job = SimpleNamespace(
        id=job_id, cos_object_key="x/y/z.mp4",
        enable_audio_analysis=True, audio_language="zh",
    )
    step = SimpleNamespace(id=uuid4(), output_artifact_path=None)
    session = SimpleNamespace()

    result = await audio_transcription.execute(session, job, step)

    assert _FakeRecognizer.last_instance.device == "cpu"

    summary = result["output_summary"]
    assert summary["audio_source"] == "cos_preprocessed"
    assert summary["whisper_device"] == "cpu"
    assert summary["skipped"] is False
    # transcript.json written
    path = Path(result["output_artifact_path"])
    assert path.exists()
    assert path.name == "transcript.json"


async def test_audio_transcription_skipped_when_has_audio_false(
    tmp_path, monkeypatch,
):
    """Download_video produced no audio.wav (upstream has_audio=false) → skipped."""
    job_id = uuid4()
    download_dir = tmp_path / str(job_id)
    download_dir.mkdir()
    # Deliberately do NOT create audio.wav.

    async def fake_get_download_dir(session, job):
        return download_dir

    monkeypatch.setattr(
        audio_transcription, "_get_download_dir", fake_get_download_dir,
        raising=False,
    )

    job = SimpleNamespace(
        id=job_id, cos_object_key="x/y/z.mp4",
        enable_audio_analysis=True, audio_language="zh",
    )
    step = SimpleNamespace(id=uuid4(), output_artifact_path=None)
    session = SimpleNamespace()

    result = await audio_transcription.execute(session, job, step)
    assert result["status"].value == "skipped" or result["status"] == "skipped"
    assert "WHISPER_NO_AUDIO" in (result["output_summary"]["skip_reason"] or "")
