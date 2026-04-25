"""Tenacity-based retry decorators per step type (Feature 014, FR-021).

- I/O steps: 3 attempts (1 initial + 2 retries) × 30s fixed wait
- CPU steps: no retry — first failure is final
"""

from __future__ import annotations

from typing import Any, Callable

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from src.services.kb_extraction_pipeline.pipeline_definition import IO_STEPS
from src.models.pipeline_step import StepType


# Exceptions considered *transient* for I/O retries. Non-retriable exceptions
# (e.g. ValueError for bad input) should not be wrapped — they propagate
# immediately on the first attempt.
RETRIABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def should_retry(step_type: StepType) -> bool:
    """Whether a step type is eligible for automatic retries."""
    return step_type in IO_STEPS


async def run_with_retry(
    step_type: StepType,
    fn: Callable[[], Any],
) -> Any:
    """Invoke ``fn`` (a zero-arg async callable) with step-type-appropriate retry.

    - I/O steps: retry up to 3 total attempts on ``RETRIABLE_EXCEPTIONS`` with
      30 s fixed delay (FR-021).
    - CPU steps: run once; propagate any exception immediately.
    """
    if not should_retry(step_type):
        return await fn()

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_fixed(30),
            retry=retry_if_exception_type(RETRIABLE_EXCEPTIONS),
            reraise=True,
        ):
            with attempt:
                return await fn()
    except RetryError as exc:  # pragma: no cover — defensive
        raise exc.last_attempt.exception() or exc
