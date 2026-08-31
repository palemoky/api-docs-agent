"""Prompts and their output schemas.

Kept in one module so they can be reviewed, diffed and tuned without reading the
orchestration code. The two stages are deliberately separate calls:

  * `ANALYZE` is cheap and answers one question - does anything need to change?
    Most runs stop here, which is what keeps the agent affordable to run on every
    commit.
  * `GENERATE` runs once per affected document and returns the full new file, so
    the result can be validated and diffed before a human sees it.
"""

from __future__ import annotations

from typing import Any

SYSTEM = (
    "You are an API documentation maintainer. You read source-code diffs and decide, "
    "conservatively and precisely, whether the repository's API documentation has fallen "
    "out of sync with the code.\n\n"
    "Principles:\n"
    "- Only the externally visible HTTP contract matters: routes, methods, path and query "
    "parameters, request and response bodies, status codes, headers, auth requirements, "
    "and error shapes.\n"
    "- Internal refactors, renamed private helpers, logging, tests, performance work and "
    "formatting are NOT documentation changes, however large the diff.\n"
    "- Never invent an endpoint, field or error code that the diff does not show. If the diff "
    "is ambiguous or truncated, say so and lower your confidence rather than guessing.\n"
    "- A wrong documentation change is worse than a missing one: a human reviews your output, "
    "and their time is the scarce resource."
)


ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "needs_update": {
            "type": "boolean",
            "description": "True only if the externally visible API contract changed.",
        },
        "confidence": {
            "type": "number",
            "description": "0.0-1.0 confidence in the needs_update verdict.",
        },
        "summary": {
            "type": "string",
            "description": "One or two sentences a reviewer can read in isolation.",
        },
        "reasoning": {
            "type": "string",
            "description": "Which specific diff lines drove the verdict.",
        },
        "endpoint_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "method": {"type": "string"},
                    "path": {"type": "string"},
                    "change_type": {
                        "type": "string",
                        "enum": [
                            "added",
                            "removed",
                            "request_changed",
                            "response_changed",
                            "auth_changed",
                            "status_codes_changed",
                            "description_changed",
                        ],
                    },
                    "description": {"type": "string"},
                },
                "required": ["method", "path", "change_type", "description"],
                "additionalProperties": False,
            },
        },
        "affected_doc_files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Paths, chosen from the provided list, that must be edited.",
        },
    },
    "required": [
        "needs_update",
        "confidence",
        "summary",
        "reasoning",
        "endpoint_changes",
        "affected_doc_files",
    ],
    "additionalProperties": False,
}


def analysis_prompt(
    *,
    commit_subject: str,
    signals: str,
    diff: str,
    doc_inventory: str,
) -> str:
    return f"""Decide whether this code change requires an API documentation update.

## Commit
{commit_subject or "(no subject)"}

## Why these files were selected
{signals or "(no heuristic signals; all changed files are shown)"}

## Documentation files this repository maintains
{doc_inventory}

## Code diff
```diff
{diff}
```

Set `needs_update` to true only if the externally visible HTTP contract changed.
List in `affected_doc_files` only paths that appear in the inventory above.
"""


GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "The complete new file content, from first line to last.",
        },
        "change_note": {
            "type": "string",
            "description": "One line per edit, describing what changed and why.",
        },
    },
    "required": ["content", "change_note"],
    "additionalProperties": False,
}


def generation_prompt(
    *,
    doc_path: str,
    doc_content: str,
    guidance: str,
    style_guide: str,
    analysis_summary: str,
    endpoint_changes: str,
    diff: str,
    validation_feedback: str = "",
) -> str:
    retry_block = ""
    if validation_feedback:
        retry_block = (
            "\n## Your previous attempt was rejected\n"
            f"{validation_feedback}\n\n"
            "Fix these problems. Return the complete corrected file again.\n"
        )

    style_block = f"\n## Project style guide\n{style_guide}\n" if style_guide else ""

    return f"""Update `{doc_path}` so it matches the code after this change.

## What changed in the code
{analysis_summary}

{endpoint_changes}

## Code diff
```diff
{diff}
```

## Format rules
{guidance}
{style_block}{retry_block}
## Current content of {doc_path}
<document>
{doc_content}
</document>

Return the COMPLETE new file content in `content` - not a diff, not a fragment,
not an abbreviation. Every line that does not need to change must be reproduced
byte-for-byte. If the document already documents this change correctly, return it
unchanged and say so in `change_note`.
"""
