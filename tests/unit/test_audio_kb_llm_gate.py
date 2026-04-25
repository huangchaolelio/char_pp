"""Unit tests — Feature 015 audio_kb_extract LLM-configuration gate (T011).

Covers FR-011: when neither Venus Proxy nor OpenAI is configured, the audio
KB executor must fail fast with a ``LLM_UNCONFIGURED:`` prefixed error rather
than attempt a doomed LLM call.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.services.kb_extraction_pipeline.error_codes import LLM_UNCONFIGURED


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_audio_kb_extract_fails_fast_without_llm_config(
    tmp_path, monkeypatch
) -> None:
    """Reproduces the condition "no Venus + no OpenAI" and asserts the
    executor raises a ``RuntimeError`` whose message starts with the
    ``LLM_UNCONFIGURED:`` prefix (FR-016 contract for grep-ability)."""
    from src.services.kb_extraction_pipeline.step_executors import audio_kb_extract

    # Prepare a minimal transcript artifact so we pass the upstream-read step
    # and enter the LLM-config check.
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(
        '{"sentences": [{"start": 0.0, "end": 1.0, "text": "test", '
        '"confidence": 0.9}], "language": "zh"}'
    )

    # Build fake upstream step row returning our transcript.
    from src.models.pipeline_step import PipelineStepStatus

    upstream = MagicMock()
    upstream.status = PipelineStepStatus.success
    upstream.output_artifact_path = str(transcript_path)

    # Patch the session.execute(...) used by the executor to fetch the
    # upstream step. The executor calls scalar_one() on the result.
    async def _fake_execute(*args, **kwargs):
        result = MagicMock()
        result.scalar_one = MagicMock(return_value=upstream)
        return result

    session = MagicMock()
    session.execute = _fake_execute

    # Force get_settings to report no LLM backend configured.
    from src.config import Settings
    import src.services.kb_extraction_pipeline.step_executors.audio_kb_extract as mod

    fake_settings = Settings()  # defaults: venus_* None, openai_api_key None
    # Defensively ensure all are None even if .env was loaded during import.
    fake_settings.venus_token = None
    fake_settings.venus_base_url = None
    fake_settings.venus_model = None
    fake_settings.openai_api_key = None

    monkeypatch.setattr(mod, "get_settings", lambda: fake_settings, raising=True)

    job = MagicMock()
    job.id = "test-job-llm-gate"
    job.tech_category = "forehand_topspin"
    step = MagicMock()

    with pytest.raises(RuntimeError) as excinfo:
        await audio_kb_extract.execute(session, job, step)

    assert str(excinfo.value).startswith(f"{LLM_UNCONFIGURED}: ")
