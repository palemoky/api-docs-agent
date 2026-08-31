"""Console notifier - the zero-configuration default."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from ..models import RunResult, RunStatus
from .base import Notifier, register_notifier, render_summary

_STYLES = {
    RunStatus.PROPOSED: "green",
    RunStatus.FAILED: "red",
    RunStatus.DRY_RUN: "yellow",
}


@register_notifier
class ConsoleNotifier(Notifier):
    name = "console"

    def should_send(self, result: RunResult) -> bool:
        # The console always reports; silence is confusing when you ran it by hand.
        return True

    def send(self, result: RunResult) -> None:
        console = Console()
        console.print(
            Panel(
                render_summary(result),
                title=f"api-docs-agent · {result.status.value}",
                border_style=_STYLES.get(result.status, "blue"),
            )
        )
