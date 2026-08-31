"""Exception hierarchy.

One base class so the CLI can render a clean message for expected failures and a
traceback for genuine bugs.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all expected, user-facing failures."""


class ConfigError(AgentError):
    pass


class GitError(AgentError):
    pass


class VCSError(AgentError):
    pass


class LLMError(AgentError):
    pass


class DocError(AgentError):
    pass


class NotifyError(AgentError):
    pass
