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
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class LlmError(Exception):
    """Raised when an LLM call fails after all retries."""


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

    # ── Venus Proxy backend ────────────────────────────────────────────────────

    def _chat_venus(
        self,
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
            raise LlmError(f"Venus proxy timeout after {self._timeout_s}s: {exc}") from exc
        except requests.HTTPError as exc:
            raise LlmError(
                f"Venus proxy HTTP error {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except requests.RequestException as exc:
            raise LlmError(f"Venus proxy request error: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise LlmError(f"Venus proxy returned non-JSON response: {exc}") from exc

        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        tokens = (usage.get("prompt_tokens", 0) or 0) + (usage.get("completion_tokens", 0) or 0)
        return content, tokens

    # ── OpenAI-compatible backend ──────────────────────────────────────────────

    def _chat_openai(
        self,
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
            raise LlmError(f"OpenAI timeout: {exc}") from exc
        except openai.APIError as exc:
            raise LlmError(f"OpenAI API error: {exc}") from exc

        content = resp.choices[0].message.content or ""
        tokens = (
            resp.usage.prompt_tokens + resp.usage.completion_tokens
            if resp.usage else 0
        )
        return content, tokens
