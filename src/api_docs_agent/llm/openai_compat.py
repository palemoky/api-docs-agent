"""OpenAI-compatible backend.

One implementation covers OpenAI, Azure OpenAI, DeepSeek, Moonshot, Qwen,
OpenRouter, Together, Ollama and vLLM - anything exposing `/v1/chat/completions`.
Point `llm.base_url` at the server and set `llm.model`.
"""

from __future__ import annotations

from typing import Any

from ..errors import LLMError
from .base import LLMProvider, parse_json_object, register_provider


@register_provider
class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def _client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise LLMError(
                "The openai provider requires the SDK: pip install 'api-docs-agent[openai]'"
            ) from exc

        kwargs: dict[str, Any] = {"timeout": self.config.timeout_seconds}
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        return OpenAI(**kwargs)

    def _request_kwargs(self, max_tokens: int | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_output_tokens,
        }
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        kwargs.update(self.config.extra)
        return kwargs

    def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> str:
        response = self._client().chat.completions.create(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            **self._request_kwargs(max_tokens),
        )
        content = response.choices[0].message.content
        if not content:
            raise LLMError("Model returned an empty response")
        return content

    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Try native JSON-schema mode, then JSON mode, then prompt-only.

        Self-hosted and third-party endpoints advertise OpenAI compatibility with
        wildly different levels of support for `response_format`, so we degrade
        instead of failing.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        client = self._client()
        attempts: list[dict[str, Any]] = [
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "strict": True, "schema": schema},
                }
            },
            {"response_format": {"type": "json_object"}},
            {},
        ]
        last_error: Exception | None = None
        for extra in attempts:
            try:
                response = client.chat.completions.create(
                    messages=messages, **self._request_kwargs(max_tokens), **extra
                )
                return parse_json_object(response.choices[0].message.content or "")
            except Exception as exc:  # noqa: BLE001 - deliberate degradation path
                last_error = exc
        raise LLMError(f"Structured output failed on all fallbacks: {last_error}")
