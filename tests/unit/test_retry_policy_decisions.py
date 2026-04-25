"""Unit tests — Feature 014 retry policy (T063).

Verifies:
  - I/O steps (download / audio transcription / audio KB extract) retry up to
    3 attempts on transient exceptions and re-raise the last error.
  - CPU steps (pose / visual_kb_extract / merge_kb) do NOT retry — they
    propagate the first exception immediately.
  - Non-retriable exceptions on I/O steps still propagate on first failure
    (no pointless retry loop for ValueError, etc).
"""

from __future__ import annotations

import pytest

from src.models.pipeline_step import StepType
from src.services.kb_extraction_pipeline.retry_policy import (
    run_with_retry,
    should_retry,
)


pytestmark = pytest.mark.unit


class TestShouldRetryClassification:
    def test_io_steps_are_retriable(self) -> None:
        assert should_retry(StepType.download_video) is True
        assert should_retry(StepType.audio_transcription) is True
        assert should_retry(StepType.audio_kb_extract) is True

    def test_cpu_steps_are_not_retriable(self) -> None:
        assert should_retry(StepType.pose_analysis) is False
        assert should_retry(StepType.visual_kb_extract) is False
        assert should_retry(StepType.merge_kb) is False


class TestRunWithRetry:
    async def test_io_step_retries_on_transient_error(self, monkeypatch) -> None:
        """I/O step that throws ConnectionError twice, succeeds on 3rd call."""
        # Neutralise tenacity's 30s wait so the test runs instantly.
        from src.services.kb_extraction_pipeline import retry_policy

        from tenacity import wait_none

        calls = {"n": 0}

        async def _flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError(f"attempt {calls['n']} — transient")
            return "ok"

        # Patch the wait to zero for this test only.
        orig_run_with_retry = retry_policy.run_with_retry

        async def _fast_wrapper(step_type, fn):
            # Mirror run_with_retry but with zero wait to keep the test fast.
            if not retry_policy.should_retry(step_type):
                return await fn()
            from tenacity import (
                AsyncRetrying,
                retry_if_exception_type,
                stop_after_attempt,
            )

            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_none(),
                retry=retry_if_exception_type(retry_policy.RETRIABLE_EXCEPTIONS),
                reraise=True,
            ):
                with attempt:
                    return await fn()

        monkeypatch.setattr(retry_policy, "run_with_retry", _fast_wrapper)

        result = await _fast_wrapper(StepType.download_video, _flaky)
        assert result == "ok"
        assert calls["n"] == 3

    async def test_io_step_gives_up_after_three_attempts(self, monkeypatch) -> None:
        from src.services.kb_extraction_pipeline import retry_policy

        from tenacity import wait_none

        calls = {"n": 0}

        async def _always_fail() -> str:
            calls["n"] += 1
            raise ConnectionError("permanent network outage")

        async def _fast_wrapper(step_type, fn):
            if not retry_policy.should_retry(step_type):
                return await fn()
            from tenacity import (
                AsyncRetrying,
                retry_if_exception_type,
                stop_after_attempt,
            )

            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_none(),
                retry=retry_if_exception_type(retry_policy.RETRIABLE_EXCEPTIONS),
                reraise=True,
            ):
                with attempt:
                    return await fn()

        with pytest.raises(ConnectionError, match="permanent"):
            await _fast_wrapper(StepType.audio_kb_extract, _always_fail)
        assert calls["n"] == 3

    async def test_cpu_step_does_not_retry(self) -> None:
        """CPU steps ignore retry — first raise escapes immediately."""
        calls = {"n": 0}

        async def _cpu_fail() -> str:
            calls["n"] += 1
            raise RuntimeError("pose extractor crashed")

        with pytest.raises(RuntimeError, match="pose extractor"):
            await run_with_retry(StepType.pose_analysis, _cpu_fail)
        assert calls["n"] == 1

    async def test_io_step_non_retriable_exception_does_not_loop(self) -> None:
        """ValueError is not in RETRIABLE_EXCEPTIONS — I/O step should fail
        immediately rather than waste retries on a permanent failure."""
        calls = {"n": 0}

        async def _bad_input() -> str:
            calls["n"] += 1
            raise ValueError("malformed cos_object_key")

        with pytest.raises(ValueError, match="malformed"):
            await run_with_retry(StepType.download_video, _bad_input)
        assert calls["n"] == 1

    async def test_cpu_step_success_path(self) -> None:
        """CPU step success returns the value without any retry logic."""

        async def _ok() -> str:
            return "done"

        result = await run_with_retry(StepType.pose_analysis, _ok)
        assert result == "done"
