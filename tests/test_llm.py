"""Provider-layer behaviour that does not need a network.

The interesting property is the contract every provider must honour, not any one
vendor's wire format.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_docs_agent.config import LLMConfig
from api_docs_agent.errors import LLMError
from api_docs_agent.llm import LLMProvider, available_providers, create_llm, parse_json_object

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"needs_update": {"type": "boolean"}, "summary": {"type": "string"}},
    "required": ["needs_update", "summary"],
    "additionalProperties": False,
}


class TextOnlyProvider(LLMProvider):
    """A backend with no structured-output support, i.e. the worst case."""

    name = "text-only"

    def __init__(self, reply: str) -> None:
        super().__init__(LLMConfig(provider="text-only", model="m"))
        self.reply = reply
        self.last_prompt = ""

    def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> str:
        self.last_prompt = prompt
        return self.reply


def test_builtin_providers_are_registered() -> None:
    assert {"anthropic", "openai", "litellm", "echo"} <= set(available_providers())


def test_an_unknown_provider_names_the_alternatives() -> None:
    with pytest.raises(LLMError, match="anthropic"):
        create_llm(LLMConfig(provider="not-a-provider"))


@pytest.mark.parametrize(
    "reply",
    [
        '{"needs_update": true, "summary": "s"}',
        '```json\n{"needs_update": true, "summary": "s"}\n```',
        '```\n{"needs_update": true, "summary": "s"}\n```',
        'Sure! Here you go:\n{"needs_update": true, "summary": "s"}\nHope that helps.',
    ],
)
def test_json_is_recovered_from_the_usual_model_wrappers(reply: str) -> None:
    """Small and self-hosted models wrap JSON in fences and chatter."""
    result = TextOnlyProvider(reply).complete_json(system="s", prompt="p", schema=SCHEMA)
    assert result == {"needs_update": True, "summary": "s"}


def test_the_schema_is_put_in_the_prompt_when_there_is_no_native_support() -> None:
    provider = TextOnlyProvider('{"needs_update": false, "summary": ""}')
    provider.complete_json(system="s", prompt="analyse this", schema=SCHEMA)

    assert "analyse this" in provider.last_prompt
    assert "needs_update" in provider.last_prompt


def test_unparseable_output_raises_a_typed_error() -> None:
    with pytest.raises(LLMError, match="Could not parse"):
        TextOnlyProvider("I'm afraid I can't do that.").complete_json(
            system="s", prompt="p", schema=SCHEMA
        )


def test_an_empty_response_raises_a_typed_error() -> None:
    with pytest.raises(LLMError, match="empty"):
        parse_json_object("   ")


def test_a_bare_json_array_is_not_accepted_as_an_object() -> None:
    with pytest.raises(LLMError):
        parse_json_object("[1, 2, 3]")


def test_vendor_exceptions_become_llm_errors() -> None:
    """A provider is a plugin boundary; the pipeline only handles AgentError."""

    class ExplodingProvider(LLMProvider):
        name = "exploding"

        def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> str:
            with self._wrap_errors():
                raise TypeError("Could not resolve authentication method")

    provider = ExplodingProvider(LLMConfig(provider="exploding", model="m"))
    with pytest.raises(LLMError, match="authentication"):
        provider.complete(system="s", prompt="p")


def test_echo_provider_satisfies_any_schema() -> None:
    """It exists so the pipeline can be exercised with no key and no network."""
    result = create_llm(LLMConfig(provider="echo", model="none")).complete_json(
        system="s", prompt="p", schema=SCHEMA
    )
    assert result["needs_update"] is False
    assert isinstance(result["summary"], str)


def test_litellm_falls_back_when_the_routed_model_lacks_schema_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_docs_agent.llm.litellm_provider import LiteLLMProvider

    provider = LiteLLMProvider(LLMConfig(provider="litellm", model="ollama/qwen2.5"))
    calls: list[dict[str, Any]] = []

    def fake_completion(**kwargs: Any) -> Any:
        calls.append(kwargs)

        class Message:
            content = '{"needs_update": true, "summary": "s"}'

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        return Response()

    monkeypatch.setattr(provider, "_completion", lambda: fake_completion)
    monkeypatch.setattr(provider, "_supports_schema", lambda: False)

    assert provider.complete_json(system="s", prompt="p", schema=SCHEMA) == {
        "needs_update": True,
        "summary": "s",
    }
    assert "response_format" not in calls[0]
    assert "needs_update" in calls[0]["messages"][1]["content"]
