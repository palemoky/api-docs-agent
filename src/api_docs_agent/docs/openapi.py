"""OpenAPI 2/3 adapter.

Validation is layered so the agent degrades gracefully:
  1. the document must parse as YAML/JSON (always available);
  2. it must have the structural markers of an OpenAPI document;
  3. if `openapi-spec-validator` is installed, it must pass full spec validation.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from .base import DocAdapter, register_adapter


@register_adapter
class OpenAPIAdapter(DocAdapter):
    name = "openapi"
    extensions = (".yaml", ".yml", ".json")

    def prompt_guidance(self) -> str:
        return (
            "This document is an OpenAPI specification.\n"
            "- Preserve the existing serialization format (YAML stays YAML, JSON stays JSON), "
            "indentation style and key ordering.\n"
            "- Only touch the paths, operations, parameters, requestBody and schemas that the "
            "code change actually affects. Leave every unrelated line byte-identical.\n"
            "- Reuse existing `components.schemas` entries instead of inlining duplicates; add a "
            "new component only when the code introduces a genuinely new model.\n"
            "- Keep `$ref` targets valid - never reference a component you did not define.\n"
            "- Do not bump the `info.version` field unless the code change is breaking.\n"
            "- Descriptions should state what the field means, not restate its type."
        )

    def validate(self, content: str) -> list[str]:
        errors = super().validate(content)
        if errors:
            return errors

        try:
            document = _parse(content)
        except Exception as exc:  # noqa: BLE001 - message is surfaced to the user
            return [f"Not parseable as YAML/JSON: {exc}"]

        if not isinstance(document, dict):
            return ["Top level of an OpenAPI document must be a mapping"]
        if not ("openapi" in document or "swagger" in document):
            errors.append("Missing the top-level `openapi` (or `swagger`) version key")
        if "paths" not in document:
            errors.append("Missing the top-level `paths` object")
        if errors:
            return errors

        errors.extend(_spec_validate(document))
        errors.extend(_dangling_refs(document))
        return errors

    def summarize(self, content: str) -> str:
        """Method + path + operationId only - enough for the model to orient itself."""
        try:
            document = _parse(content)
        except Exception:  # noqa: BLE001
            return content[:4000]
        if not isinstance(document, dict):
            return content[:4000]

        lines: list[str] = []
        for path, item in (document.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            for method, operation in item.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                op_id = operation.get("operationId", "")
                summary = operation.get("summary", "")
                lines.append(f"{method.upper():7} {path}  {op_id} {summary}".rstrip())
        schemas = ((document.get("components") or {}).get("schemas") or {}).keys()
        if schemas:
            lines.append("")
            lines.append(f"components.schemas: {', '.join(sorted(schemas))}")
        return "\n".join(lines) or content[:4000]


_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _parse(content: str) -> Any:
    stripped = content.lstrip()
    if stripped.startswith("{"):
        return json.loads(content)
    return yaml.safe_load(content)


def _spec_validate(document: dict[str, Any]) -> list[str]:
    try:
        from openapi_spec_validator import validate  # type: ignore[import-not-found]
    except ImportError:
        return []
    try:
        validate(document)
    except Exception as exc:  # noqa: BLE001 - the validator raises many types
        return [f"OpenAPI spec validation failed: {exc}"]
    return []


def _dangling_refs(document: dict[str, Any]) -> list[str]:
    """Catch `$ref: '#/components/schemas/Foo'` where Foo was never defined.

    This is the single most common way an LLM breaks a spec, and the full spec
    validator is an optional dependency, so it is worth checking directly.
    """
    refs: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/"):
                refs.add(ref)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)

    errors: list[str] = []
    for ref in sorted(refs):
        cursor: Any = document
        for part in ref.lstrip("#/").split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                errors.append(f"Dangling reference: {ref}")
                break
    return errors
