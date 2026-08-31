from __future__ import annotations

from api_docs_agent.docs import get_adapter

VALID = """\
openapi: 3.0.3
info:
  title: T
  version: 1.0.0
paths:
  /users:
    get:
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/User'
components:
  schemas:
    User:
      type: object
      properties:
        id:
          type: integer
"""


def test_adapter_is_inferred_from_the_extension() -> None:
    assert get_adapter("auto", "docs/openapi.yaml").name == "openapi"
    assert get_adapter("auto", "docs/api.md").name == "markdown"
    assert get_adapter("markdown", "docs/openapi.yaml").name == "markdown"


def test_valid_spec_passes() -> None:
    assert get_adapter("openapi", "x.yaml").validate(VALID) == []


def test_broken_yaml_is_rejected() -> None:
    errors = get_adapter("openapi", "x.yaml").validate("openapi: 3.0.3\n  bad: [indent\n")
    assert errors and "parseable" in errors[0]


def test_dangling_ref_is_rejected() -> None:
    """The most common way an LLM silently breaks a spec."""
    broken = VALID.replace("#/components/schemas/User", "#/components/schemas/Account")
    errors = get_adapter("openapi", "x.yaml").validate(broken)
    assert any("Dangling reference" in e or "Account" in e for e in errors)


def test_missing_paths_key_is_rejected() -> None:
    errors = get_adapter("openapi", "x.yaml").validate("openapi: 3.0.3\ninfo: {}\n")
    assert any("paths" in e for e in errors)


def test_openapi_summary_lists_operations() -> None:
    summary = get_adapter("openapi", "x.yaml").summarize(VALID)
    assert "GET" in summary and "/users" in summary
    assert "components.schemas: User" in summary


def test_truncated_markdown_is_rejected() -> None:
    errors = get_adapter("markdown", "x.md").validate("# API\n\n```json\n{\n")
    assert any("fence" in e for e in errors)


def test_markdown_without_headings_is_rejected() -> None:
    assert get_adapter("markdown", "x.md").validate("just some prose")
