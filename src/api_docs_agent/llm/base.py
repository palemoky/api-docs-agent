"""The provider-agnostic LLM interface.

The whole point of this module: the pipeline never imports a vendor SDK. It asks
a `LLMProvider` for text or for JSON matching a schema, and the concrete provider
decides how to get it. Adding a vendor means writing `complete()` and calling
`register_provider()` - nothing else in the codebase changes.
"""

from __future__ import annotations

import abc
import json
import re
from typing import Any

from ..config import LLMConfig
from ..errors import LLMError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMProvider(abc.ABC):
    """Base class for every model backend."""

    #: Registry key used in config (`llm.provider`).
    name: str = ""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @abc.abstractmethod
    def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> str:
        """Return the model's text response for a single-turn request."""

    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Return a dict matching `schema`.

        The default implementation asks for JSON in the prompt and repairs the
        common failure modes (code fences, leading prose). Providers with native
        structured output should override this - it is strictly more reliable.
        """
        instructed = (
            f"{prompt}\n\n"
            "Respond with a single JSON object and nothing else. It must conform to "
            f"this JSON Schema:\n{json.dumps(schema, indent=2)}"
        )
        raw = self.complete(system=system, prompt=instructed, max_tokens=max_tokens)
        return parse_json_object(raw)

    def describe(self) -> str:
        return f"{self.name}:{self.config.model}"


def parse_json_object(raw: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from a model response."""
    text = raw.strip()
    if not text:
        raise LLMError("Model returned an empty response")

    candidates = [text]
    fenced = _FENCE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LLMError(f"Could not parse a JSON object from the model response:\n{text[:800]}")


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_REGISTRY: dict[str, type[LLMProvider]] = {}


def register_provider(cls: type[LLMProvider]) -> type[LLMProvider]:
    """Class decorator that makes a provider selectable from config."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must define a non-empty `name`")
    _REGISTRY[cls.name] = cls
    return cls


def available_providers() -> list[str]:
    _load_builtin_providers()
    return sorted(_REGISTRY)


def create_llm(config: LLMConfig) -> LLMProvider:
    _load_builtin_providers()
    try:
        cls = _REGISTRY[config.provider]
    except KeyError:
        raise LLMError(
            f"Unknown LLM provider {config.provider!r}. "
            f"Available: {', '.join(available_providers()) or '(none)'}"
        ) from None
    return cls(config)


def _load_builtin_providers() -> None:
    """Import built-in providers lazily so a missing optional SDK is not fatal."""
    from . import anthropic_provider, echo, openai_compat  # noqa: F401
