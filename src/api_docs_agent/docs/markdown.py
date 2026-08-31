"""Markdown adapter.

Markdown cannot be validated against a schema, so validation here is limited to
structural sanity checks that catch a truncated or mangled generation.
"""

from __future__ import annotations

import re

from .base import DocAdapter, register_adapter

_HEADING = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)


@register_adapter
class MarkdownAdapter(DocAdapter):
    name = "markdown"
    extensions = (".md", ".markdown", ".mdx")

    def prompt_guidance(self) -> str:
        return (
            "This document is Markdown API reference prose.\n"
            "- Match the surrounding document's structure exactly: same heading levels, same "
            "table columns, same order of sections per endpoint.\n"
            "- Edit only the sections the code change affects; leave every other line "
            "byte-identical.\n"
            "- Keep request/response examples runnable and consistent with the new field names, "
            "types and status codes.\n"
            "- Do not invent fields, error codes or endpoints that are absent from the code."
        )

    def validate(self, content: str) -> list[str]:
        errors = super().validate(content)
        if errors:
            return errors
        if content.count("```") % 2 != 0:
            errors.append("Unbalanced ``` code fence - the output looks truncated")
        if not _HEADING.search(content):
            errors.append("No Markdown heading found - the document structure was lost")
        return errors

    def summarize(self, content: str) -> str:
        outline = [line for line in content.splitlines() if line.startswith("#")]
        return "\n".join(outline) if outline else content[:4000]
