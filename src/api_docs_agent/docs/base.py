"""Documentation format adapters.

An adapter knows three things about a documentation format: how to recognise it,
what to tell the model about editing it, and how to check the model's output
before a human ever sees it. That last one is what separates this from "ask an
LLM to rewrite a file" - a proposal that fails validation never reaches a PR.
"""

from __future__ import annotations

import abc
from pathlib import Path

_REGISTRY: dict[str, type[DocAdapter]] = {}


class DocAdapter(abc.ABC):
    #: Registry key used in config (`docs.sources[].type`).
    name: str = ""
    #: File extensions this adapter claims when `type: auto`.
    extensions: tuple[str, ...] = ()

    @abc.abstractmethod
    def prompt_guidance(self) -> str:
        """Format-specific rules injected into the generation prompt."""

    def validate(self, content: str) -> list[str]:
        """Return a list of human-readable problems; empty means valid."""
        return [] if content.strip() else ["Document is empty"]

    def summarize(self, content: str) -> str:
        """A compact view of the doc, for prompts where the full text is too big."""
        return content


def register_adapter(cls: type[DocAdapter]) -> type[DocAdapter]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} must define a non-empty `name`")
    _REGISTRY[cls.name] = cls
    return cls


def available_adapters() -> list[str]:
    _load_builtin_adapters()
    return sorted(_REGISTRY)


def get_adapter(type_name: str, path: str) -> DocAdapter:
    _load_builtin_adapters()
    if type_name and type_name != "auto":
        if type_name not in _REGISTRY:
            raise KeyError(
                f"Unknown doc type {type_name!r}. Available: {', '.join(sorted(_REGISTRY))}"
            )
        return _REGISTRY[type_name]()

    suffix = Path(path).suffix.lower()
    for cls in _REGISTRY.values():
        if suffix in cls.extensions:
            return cls()
    return _REGISTRY["markdown"]()


def _load_builtin_adapters() -> None:
    from . import markdown, openapi  # noqa: F401
