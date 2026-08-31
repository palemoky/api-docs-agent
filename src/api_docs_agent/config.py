"""Configuration loading.

Config comes from a YAML file (checked into the target repo as
`.api-docs-agent.yml`) with `${ENV_VAR}` interpolation, so secrets stay in the
environment while behaviour stays in version control.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_NAMES = (
    ".api-docs-agent.yml",
    ".api-docs-agent.yaml",
    "api-docs-agent.yml",
)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class LLMConfig(BaseModel):
    """Which model to talk to.

    `provider` selects a registered implementation; everything else is passed
    through, so pointing at Ollama, vLLM, DeepSeek or Azure is a config change,
    not a code change.
    """

    provider: str = "anthropic"
    model: str = "claude-opus-5"
    api_key: str | None = None
    base_url: str | None = None
    max_output_tokens: int = 16000
    temperature: float | None = None
    timeout_seconds: float = 300.0
    extra: dict[str, Any] = Field(default_factory=dict)


class DetectConfig(BaseModel):
    """Cheap, deterministic pre-filter run before any token is spent."""

    watch_paths: list[str] = Field(
        default_factory=lambda: [
            "**/*.py",
            "**/*.go",
            "**/*.java",
            "**/*.ts",
            "**/*.js",
            "**/*.rb",
            "**/*.php",
        ]
    )
    ignore_paths: list[str] = Field(
        default_factory=lambda: [
            "**/test/**",
            "**/tests/**",
            "**/*_test.*",
            "**/*.test.*",
            "**/node_modules/**",
            "**/vendor/**",
            "**/migrations/**",
        ]
    )
    # Regexes that suggest a changed line touches the HTTP surface. Language
    # agnostic on purpose - add your framework's idiom here.
    api_signals: list[str] = Field(
        default_factory=lambda: [
            r"@(app|router|blueprint|api)\.(get|post|put|patch|delete|route)",
            r"@(Get|Post|Put|Patch|Delete|Request)Mapping",
            r"@(RestController|Controller)\b",
            r"\b(router|mux|r|e)\.(GET|POST|PUT|PATCH|DELETE|Handle)\b",
            r"\bapp\.(get|post|put|patch|delete)\s*\(",
            r"\bRoute::(get|post|put|patch|delete|apiResource)\b",
            r"\b(BaseModel|Schema|DTO|Serializer)\b",
            r"\bresponse_model\s*=",
            r"\bstatus_code\s*=",
        ]
    )
    # If true, a diff with zero signal matches still goes to the LLM (slower,
    # more expensive, fewer misses).
    always_ask_llm: bool = False
    max_diff_chars: int = 60000


class DocSource(BaseModel):
    """One documentation artifact the agent is allowed to edit."""

    path: str
    # Which adapter validates and prompts for this file. "auto" infers from the
    # extension.
    type: str = "auto"
    description: str = ""


class DocsConfig(BaseModel):
    sources: list[DocSource] = Field(default_factory=list)
    # Extra guidance injected into the generation prompt, e.g. house style rules.
    style_guide: str = ""


class GitHubConfig(BaseModel):
    repository: str | None = None  # "owner/name"; inferred from the remote if unset
    token: str | None = None
    api_url: str = "https://api.github.com"
    base_branch: str | None = None  # defaults to the repo's default branch
    branch_prefix: str = "docs/api-sync"
    labels: list[str] = Field(default_factory=lambda: ["documentation", "automated"])
    reviewers: list[str] = Field(default_factory=list)
    draft: bool = False


class NotifierConfig(BaseModel):
    type: str
    enabled: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    detect: DetectConfig = Field(default_factory=DetectConfig)
    docs: DocsConfig = Field(default_factory=DocsConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    notifiers: list[NotifierConfig] = Field(
        default_factory=lambda: [NotifierConfig(type="console")]
    )
    # Below this confidence the agent still opens a PR but flags it as low
    # confidence in the body, so a human knows to look harder.
    low_confidence_threshold: float = 0.5

    @classmethod
    def load(cls, path: str | Path | None, repo_root: Path) -> AgentConfig:
        config_path = _resolve_config_path(path, repo_root)
        if config_path is None:
            return cls()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(_expand_env(raw))


def _resolve_config_path(path: str | Path | None, repo_root: Path) -> Path | None:
    if path is not None:
        candidate = Path(path)
        if not candidate.is_file():
            raise FileNotFoundError(f"Config file not found: {candidate}")
        return candidate
    for name in DEFAULT_CONFIG_NAMES:
        candidate = repo_root / name
        if candidate.is_file():
            return candidate
    return None


def _expand_env(value: Any) -> Any:
    """Recursively replace ${VAR} and ${VAR:-default} in strings."""
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")

        return _ENV_PATTERN.sub(replace, value)
    return value
