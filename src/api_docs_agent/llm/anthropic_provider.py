"""Anthropic (Claude) backend - the default provider.

Uses native structured outputs (`output_config.format`) so stage-1 analysis comes
back as guaranteed-valid JSON instead of prose we have to repair.
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import LLMError
from .base import LLMProvider, register_provider


@register_provider
class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def _client(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise LLMError(
                "The anthropic provider requires the SDK: pip install 'api-docs-agent[anthropic]'"
            ) from exc

        kwargs: dict[str, Any] = {"timeout": self.config.timeout_seconds}
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        return anthropic.Anthropic(**kwargs)

    def _request_kwargs(self, max_tokens: int | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_output_tokens,
        }
        kwargs.update(self.config.extra)
        return kwargs

    def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> str:
        with self._wrap_errors():
            client = self._client()
            with client.messages.stream(
                system=system,
                messages=[{"role": "user", "content": prompt}],
                **self._request_kwargs(max_tokens),
            ) as stream:
                message = stream.get_final_message()
            return _first_text(message)

    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        with self._wrap_errors():
            client = self._client()
            with client.messages.stream(
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
                **self._request_kwargs(max_tokens),
            ) as stream:
                message = stream.get_final_message()
            return json.loads(_first_text(message))


def _first_text(message: Any) -> str:
    if getattr(message, "stop_reason", None) == "refusal":
        details = getattr(message, "stop_details", None)
        raise LLMError(f"The model declined the request: {details}")
    for block in message.content:
        if block.type == "text":
            return block.text
    raise LLMError("Model response contained no text block")
