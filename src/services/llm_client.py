"""Unified LLM client — supports multiple backends.

Priority order:
  1. Venus Proxy  (venus_token + venus_base_url configured)  — raw HTTP requests
  2. OpenAI-compatible SDK  (openai_api_key + base_url)      — openai package

Venus Proxy request format (OpenAI chat/completions compatible):
  POST {venus_base_url}
  Headers: {"Authorization": "Bearer {venus_token}", "Content-Type": "application/json"}
  Body: {"model": ..., "messages": [...], "temperature": ..., "response_format": ...}

Usage:
  client = LlmClient.from_settings()
  response_text, tokens = client.chat(messages, model, temperature, json_mode=True)

Feature-023 / Path 1' — 限流退避:
  Venus Proxy 公共服务 RPM=50。为避免全量扫描期间云集中击穿限流导致批量失败，
  在 _chat_venus / _chat_openai 外层包裹 tenacity 指数退避:
    · 仅对 HTTP 429 与 5xx 类错误重试（可重试子集 RetryableLlmError）
    · 最多 5 次；wait = exponential(min=10, max=60) 秒
    · 代码逻辑错误（账号未配置/请求参数错误等）不重试，fail fast
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class LlmError(Exception):
    """Raised when an LLM call fails after all retries."""


class RetryableLlmError(LlmError):
    """Subclass marking transient errors that warrant tenacity retry.

    触发条件：HTTP 429 (Too Many Requests) 或 5xx 上游服务错误。
    代码逻辑/参数错误仍招 `LlmError` 原型不重试。
    """


# Feature-023 / Path 1' — LLM 访问限流退避装饰器
# Venus Proxy 公共服务 RPM=50；全量扫描中未被规则命中的少数视频会串行
# 压入 LLM 兑底路，需退避以平滑冲击。
_LLM_RETRY_DECORATOR = retry(
    retry=retry_if_exception_type(RetryableLlmError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=10, max=60),
    reraise=True,
)


class LlmClient:
    """Unified LLM client with venus_proxy priority and openai-compatible fallback."""

    def __init__(
        self,
        *,
        # Venus Proxy (priority)
        venus_token: Optional[str] = None,
        venus_base_url: Optional[str] = None,
        venus_model: Optional[str] = None,
        # OpenAI-compatible fallback
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        openai_model: str = "gpt-4o-mini",
        # Shared
        timeout_s: int = 30,
    ) -> None:
        self._timeout_s = timeout_s

        # Determine active backend
        if venus_token and venus_base_url:
            self._backend = "venus"
            self._venus_token = venus_token
            self._venus_base_url = venus_base_url.rstrip("/")
            self._default_model = venus_model or openai_model
            logger.info("llm_client backend=venus_proxy model=%s", self._default_model)
        elif openai_api_key:
            self._backend = "openai"
            self._openai_api_key = openai_api_key
            self._openai_base_url = openai_base_url
            self._default_model = openai_model
            logger.info(
                "llm_client backend=openai_compatible base_url=%s model=%s",
                openai_base_url or "api.openai.com",
                self._default_model,
            )
        else:
            raise ValueError(
                "LlmClient requires either (venus_token + venus_base_url) "
                "or openai_api_key to be configured."
            )

    @classmethod
    def from_settings(cls) -> "LlmClient":
        """Create client from application settings (reads .env via pydantic-settings)."""
        from src.config import get_settings
        s = get_settings()
        return cls(
            venus_token=s.venus_token,
            venus_base_url=s.venus_base_url,
            venus_model=s.venus_model,
            openai_api_key=s.openai_api_key,
            openai_base_url=s.openai_base_url or s.base_url,
            openai_model=s.llm_model or s.openai_model,
            timeout_s=s.openai_timeout_s,
        )

    def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> tuple[str, int]:
        """Send a chat completion request.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            model: Override model (uses default if None).
            temperature: Sampling temperature.
            json_mode: If True, request JSON response format.

        Returns:
            (response_text, total_tokens) — tokens is 0 if unavailable.

        Raises:
            LlmError: On API error or timeout.
        """
        effective_model = model or self._default_model
        if self._backend == "venus":
            return self._chat_venus(messages, effective_model, temperature, json_mode)
        else:
            return self._chat_openai(messages, effective_model, temperature, json_mode)

    # ── Venus Proxy backend ────────────────────────────────────────────────

    @_LLM_RETRY_DECORATOR
    def _chat_venus(        self,
        messages: list[dict],
        model: str,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, int]:
        url = self._venus_base_url
        headers = {
            "Authorization": f"Bearer {self._venus_token}",
            "Content-Type": "application/json",
        }
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = requests.post(
                url, headers=headers, data=json.dumps(payload),
                timeout=self._timeout_s,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            # 超时归为可重试（网络抖动）
            raise RetryableLlmError(
                f"Venus proxy timeout after {self._timeout_s}s: {exc}"
            ) from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            body_snippet = (
                exc.response.text[:200] if exc.response is not None else ""
            )
            # 限流 / 上游 5xx 可重试；其他 4xx 不重试
            if status == 429 or 500 <= status < 600:
                raise RetryableLlmError(
                    f"Venus proxy HTTP {status} (retryable): {body_snippet}"
                ) from exc
            raise LlmError(
                f"Venus proxy HTTP error {status}: {body_snippet}"
            ) from exc
        except requests.RequestException as exc:
            # 连接重置/DNS 临时性问题 → 可重试
            raise RetryableLlmError(
                f"Venus proxy request error (retryable): {exc}"
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise LlmError(f"Venus proxy returned non-JSON response: {exc}") from exc

        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        tokens = (usage.get("prompt_tokens", 0) or 0) + (usage.get("completion_tokens", 0) or 0)
        return content, tokens

    # ── OpenAI-compatible backend ────────────────────────────────────────────

    @_LLM_RETRY_DECORATOR
    def _chat_openai(        self,
        messages: list[dict],
        model: str,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, int]:
        import openai

        client_kwargs: dict = {
            "api_key": self._openai_api_key,
            "timeout": self._timeout_s,
        }
        if self._openai_base_url:
            client_kwargs["base_url"] = self._openai_base_url

        client = openai.OpenAI(**client_kwargs)
        extra: dict = {}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                **extra,
            )
        except openai.APITimeoutError as exc:
            raise RetryableLlmError(f"OpenAI timeout (retryable): {exc}") from exc
        except openai.RateLimitError as exc:
            raise RetryableLlmError(
                f"OpenAI rate limit hit (retryable): {exc}"
            ) from exc
        except openai.APIError as exc:
            # APIError 包含 5xx 上游问题；选择以 status_code 判断
            status = getattr(exc, "status_code", None) or 0
            if 500 <= status < 600:
                raise RetryableLlmError(
                    f"OpenAI upstream {status} (retryable): {exc}"
                ) from exc
            raise LlmError(f"OpenAI API error: {exc}") from exc

        content = resp.choices[0].message.content or ""
        tokens = (
            resp.usage.prompt_tokens + resp.usage.completion_tokens
            if resp.usage else 0
        )
        return content, tokens
