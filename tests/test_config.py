from __future__ import annotations

from pathlib import Path

import pytest

from api_docs_agent.config import AgentConfig

CONFIG = """\
llm:
  provider: openai
  model: ${MODEL_NAME:-fallback-model}
  api_key: ${SOME_KEY}
  base_url: ${MISSING_URL}
docs:
  sources:
    - path: docs/openapi.yaml
      type: openapi
github:
  token: ${GH_TOKEN}
notifiers:
  - type: feishu
    options:
      webhook_url: ${HOOK}
"""


def test_env_interpolation_with_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_KEY", "sk-123")
    monkeypatch.setenv("HOOK", "https://hook.invalid")
    monkeypatch.delenv("MODEL_NAME", raising=False)

    path = tmp_path / ".api-docs-agent.yml"
    path.write_text(CONFIG, encoding="utf-8")
    config = AgentConfig.load(path, tmp_path)

    assert config.llm.model == "fallback-model"
    assert config.llm.api_key == "sk-123"
    assert config.notifiers[0].options["webhook_url"] == "https://hook.invalid"


def test_unset_variables_become_none_not_empty_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty string would shadow each SDK's own credential discovery."""
    for name in ("SOME_KEY", "MISSING_URL", "GH_TOKEN", "HOOK", "MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)

    path = tmp_path / ".api-docs-agent.yml"
    path.write_text(CONFIG, encoding="utf-8")
    config = AgentConfig.load(path, tmp_path)

    assert config.llm.api_key is None
    assert config.llm.base_url is None
    assert config.github.token is None


def test_a_missing_config_file_yields_usable_defaults(tmp_path: Path) -> None:
    config = AgentConfig.load(None, tmp_path)
    assert config.llm.provider == "anthropic"
    assert config.docs.sources == []
    assert [n.type for n in config.notifiers] == ["console"]


def test_config_is_discovered_by_name(tmp_path: Path) -> None:
    (tmp_path / ".api-docs-agent.yml").write_text("llm:\n  model: m\n", encoding="utf-8")
    assert AgentConfig.load(None, tmp_path).llm.model == "m"


def test_an_explicitly_named_missing_config_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        AgentConfig.load(tmp_path / "nope.yml", tmp_path)
