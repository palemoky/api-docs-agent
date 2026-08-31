"""A dependency-free fake provider.

Used by the test suite and by `--llm-provider echo`, so the whole pipeline
(diff -> analysis -> proposal -> branch -> PR body) can be exercised without an
API key or a network. It never claims documentation needs updating.
"""

from __future__ import annotations

import json
from typing import Any

from .base import LLMProvider, register_provider


@register_provider
class EchoProvider(LLMProvider):
    name = "echo"

    def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> str:
        return "echo provider: no model configured"

    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        properties: dict[str, Any] = schema.get("properties", {})
        stub: dict[str, Any] = {}
        for key in schema.get("required", properties.keys()):
            stub[key] = _zero_value(properties.get(key, {}))
        if "summary" in stub:
            stub["summary"] = "echo provider - no analysis performed"
        return json.loads(json.dumps(stub))


def _zero_value(spec: dict[str, Any]) -> Any:
    match spec.get("type"):
        case "boolean":
            return False
        case "number" | "integer":
            return 0
        case "array":
            return []
        case "object":
            return {}
        case _:
            return ""
