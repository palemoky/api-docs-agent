"""Notification channels.

A notifier receives the finished `RunResult` and tells a human about it. They are
fire-and-forget by design: a Feishu outage must never fail a run that already
opened a correct pull request.
"""

from __future__ import annotations

import abc
import logging
from typing import Any

from ..config import NotifierConfig
from ..models import RunResult, RunStatus

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[Notifier]] = {}


class Notifier(abc.ABC):
    #: Registry key used in config (`notifiers[].type`).
    name: str = ""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}

    @abc.abstractmethod
    def send(self, result: RunResult) -> None: ...

    def should_send(self, result: RunResult) -> bool:
        """By default only speak up when there is something to review."""
        notify_on = self.options.get("notify_on", ["proposed", "failed"])
        return result.status.value in notify_on


def register_notifier(cls: type[Notifier]) -> type[Notifier]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} must define a non-empty `name`")
    _REGISTRY[cls.name] = cls
    return cls


def available_notifiers() -> list[str]:
    _load_builtin_notifiers()
    return sorted(_REGISTRY)


def build_notifiers(configs: list[NotifierConfig]) -> list[Notifier]:
    _load_builtin_notifiers()
    notifiers: list[Notifier] = []
    for config in configs:
        if not config.enabled:
            continue
        cls = _REGISTRY.get(config.type)
        if cls is None:
            logger.warning(
                "Unknown notifier %r; available: %s", config.type, ", ".join(sorted(_REGISTRY))
            )
            continue
        notifiers.append(cls(config.options))
    return notifiers


def dispatch(notifiers: list[Notifier], result: RunResult) -> None:
    for notifier in notifiers:
        if not notifier.should_send(result):
            continue
        try:
            notifier.send(result)
        except Exception as exc:  # noqa: BLE001 - notification must never break a run
            logger.warning("Notifier %s failed: %s", notifier.name, exc)


def render_summary(result: RunResult) -> str:
    """Plain-text body shared by every channel."""
    lines: list[str] = []
    change = result.change

    if result.status is RunStatus.PROPOSED and result.pull_request:
        lines.append("API documentation may be out of date after a recent change.")
        lines.append("")
        lines.append(f"Review the proposed update: {result.pull_request.url}")
    elif result.status is RunStatus.FAILED:
        lines.append(f"api-docs-agent failed: {result.message}")
    else:
        lines.append(f"api-docs-agent finished with status: {result.status.value}")
        if result.message:
            lines.append(result.message)

    if change:
        lines += [
            "",
            f"Commit:  {change.commit_sha[:10]}  {change.subject}",
            f"Author:  {change.author_name} <{change.author_email}>",
        ]
    if result.analysis:
        lines += ["", f"Analysis: {result.analysis.summary}"]
        for endpoint in result.analysis.endpoint_changes:
            lines.append(
                f"  - {endpoint.method} {endpoint.path} "
                f"[{endpoint.change_type}] {endpoint.description}"
            )
    if result.proposals:
        lines += ["", "Documents updated:"]
        lines += [f"  - {p.doc_path}" for p in result.proposals]
    return "\n".join(lines)


def _load_builtin_notifiers() -> None:
    from . import console, email, feishu  # noqa: F401
