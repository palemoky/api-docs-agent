"""LiteLLM backend - breadth over depth.

The `anthropic` and `openai` providers cover the two API shapes that matter, with
native structured output on each. This one trades that for reach: LiteLLM
normalises 100+ vendors behind one call, so Bedrock, Vertex, Azure, Gemini,
Cohere, Ollama and the rest become a model string.

Prefer the dedicated providers when they fit; reach for this when your vendor is
neither Anthropic- nor OpenAI-shaped.
"""

from __future__ import annotations

from typing import Any

from ..errors import LLMError
from .base import LLMProvider, parse_json_object, register_provider


@register_provider
class LiteLLMProvider(LLMProvider):
    name = "litellm"

    def _completion(self) -> Any:
        try:
            from litellm import completion
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise LLMError(
                "The litellm provider requires the SDK: pip install 'api-docs-agent[litellm]'"
            ) from exc
        return completion

    def _request_kwargs(self, max_tokens: int | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_output_tokens,
            "timeout": self.config.timeout_seconds,
        }
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        kwargs.update(self.config.extra)
        return kwargs

    def _messages(self, system: str, prompt: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> str:
        with self._wrap_errors():
            response = self._completion()(
                messages=self._messages(system, prompt), **self._request_kwargs(max_tokens)
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
        """Use native JSON schema where the routed model supports it.

        `supports_response_schema` asks LiteLLM about the *target* model rather
        than guessing from the provider name, so a run against Bedrock Claude and
        a run against a local Ollama model each take the best path available.
        """
        with self._wrap_errors():
            completion = self._completion()
            kwargs = self._request_kwargs(max_tokens)
            messages = self._messages(system, prompt)

            if self._supports_schema():
                response = completion(
                    messages=messages,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": "result", "strict": True, "schema": schema},
                    },
                    **kwargs,
                )
                return parse_json_object(response.choices[0].message.content or "")

        # No native support: fall back to the base class's prompt-and-repair path.
        return super().complete_json(
            system=system, prompt=prompt, schema=schema, max_tokens=max_tokens
        )

    def _supports_schema(self) -> bool:
        try:
            from litellm import supports_response_schema

            return bool(supports_response_schema(model=self.config.model))
        except Exception:  # noqa: BLE001 - an unknown model is not a failure
            return False
