"""Command line interface.

The CLI is the only entry point: GitHub Actions, a git hook, a cron job or a
future webhook service all end up calling `api-docs-agent check`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from . import __version__
from .config import AgentConfig
from .docs import available_adapters, get_adapter
from .errors import AgentError
from .llm import available_providers
from .models import RunStatus
from .notify import available_notifiers
from .pipeline import DocSyncPipeline
from .vcs import GitRepo

app = typer.Typer(
    name="api-docs-agent",
    help="Keep API documentation in sync with code by proposing pull requests.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# Exit codes are part of the contract with CI.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PROPOSED = 0  # a proposal is a success, not a build failure


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _load(repo_path: Path, config_path: Path | None) -> tuple[AgentConfig, GitRepo]:
    repo = GitRepo(repo_path)
    config = AgentConfig.load(config_path, repo.root)
    return config, repo


@app.command()
def check(
    base: str = typer.Option(
        None, "--base", "-b", help="Base ref to diff from. Defaults to the parent commit."
    ),
    head: str = typer.Option("HEAD", "--head", "-H", help="Head ref to diff to."),
    repo_path: Path = typer.Option(Path("."), "--repo", "-r", help="Path to the git repository."),
    config_path: Path = typer.Option(
        None, "--config", "-c", help="Config file. Defaults to .api-docs-agent.yml in the repo."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Analyse and generate, but never push or open a pull request."
    ),
    llm_provider: str = typer.Option(None, "--llm-provider", help="Override llm.provider."),
    model: str = typer.Option(None, "--model", "-m", help="Override llm.model."),
    no_notify: bool = typer.Option(False, "--no-notify", help="Skip every notifier."),
    show_diff: bool = typer.Option(
        False, "--show-diff", help="Print the proposed documentation diff."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Analyse a commit range and open a documentation pull request if needed."""
    _setup_logging(verbose)
    try:
        config, repo = _load(repo_path, config_path)
        if llm_provider:
            config.llm.provider = llm_provider
        if model:
            config.llm.model = model
        if not config.docs.sources:
            raise AgentError(
                "No documentation sources configured. Add docs.sources to your config "
                "(run `api-docs-agent init` to generate one)."
            )

        pipeline = DocSyncPipeline(config, repo)
        result = pipeline.run(base_ref=base, head_ref=head, dry_run=dry_run, notify=not no_notify)
    except AgentError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_FAILED) from exc

    if show_diff:
        import difflib

        for proposal in result.proposals:
            diff = "\n".join(
                difflib.unified_diff(
                    proposal.original_content.splitlines(),
                    proposal.proposed_content.splitlines(),
                    fromfile=f"a/{proposal.doc_path}",
                    tofile=f"b/{proposal.doc_path}",
                    lineterm="",
                )
            )
            console.print(Syntax(diff or "(no changes)", "diff", theme="ansi_dark"))

    raise typer.Exit(EXIT_FAILED if result.status is RunStatus.FAILED else EXIT_OK)


@app.command()
def inspect(
    base: str = typer.Option(None, "--base", "-b"),
    head: str = typer.Option("HEAD", "--head", "-H"),
    repo_path: Path = typer.Option(Path("."), "--repo", "-r"),
    config_path: Path = typer.Option(None, "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show what the pre-filter sees, without calling a model.

    Use this to tune `detect.api_signals` against your codebase for free.
    """
    _setup_logging(verbose)
    from .detect import prefilter

    config, repo = _load(repo_path, config_path)
    base = base or repo.default_base_ref(head)
    change = repo.collect_changes(base, head)
    result = prefilter(change, config.detect, repo)

    console.print(f"[bold]{change.commit_sha[:10]}[/bold] {change.subject}")
    console.print(f"comparing {base} .. {head}\n")

    table = Table("file", "verdict", "why")
    for signal in result.signals:
        table.add_row(signal.path, "[green]candidate[/green]", "; ".join(signal.reasons) or "-")
    for path in result.skipped_files:
        table.add_row(path, "[dim]skipped[/dim]", "-")
    console.print(table)
    console.print(
        f"\n{len(result.relevant_files)} candidate file(s); "
        f"{'a model call would follow' if result.has_candidates else 'no model call needed'}."
    )


@app.command()
def validate(
    repo_path: Path = typer.Option(Path("."), "--repo", "-r"),
    config_path: Path = typer.Option(None, "--config", "-c"),
) -> None:
    """Check the configuration and validate every configured documentation file."""
    config, repo = _load(repo_path, config_path)
    console.print(f"llm:       {config.llm.provider} / {config.llm.model}")
    console.print(f"notifiers: {', '.join(n.type for n in config.notifiers) or '(none)'}")

    if not config.docs.sources:
        console.print("[yellow]warning:[/yellow] no docs.sources configured")

    failed = False
    table = Table("document", "format", "status")
    for source in config.docs.sources:
        adapter = get_adapter(source.type, source.path)
        content = repo.read_file(source.path)
        if not content:
            table.add_row(source.path, adapter.name, "[red]missing[/red]")
            failed = True
            continue
        errors = adapter.validate(content)
        if errors:
            failed = True
            table.add_row(source.path, adapter.name, "[red]" + "; ".join(errors) + "[/red]")
        else:
            table.add_row(source.path, adapter.name, "[green]valid[/green]")
    console.print(table)
    raise typer.Exit(EXIT_FAILED if failed else EXIT_OK)


@app.command()
def providers() -> None:
    """List the registered LLM providers, doc formats and notifiers."""
    table = Table("extension point", "registered implementations")
    table.add_row("LLM providers", ", ".join(available_providers()))
    table.add_row("Doc formats", ", ".join(available_adapters()))
    table.add_row("Notifiers", ", ".join(available_notifiers()))
    console.print(table)


@app.command()
def init(
    repo_path: Path = typer.Option(Path("."), "--repo", "-r"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config."),
) -> None:
    """Write a starter .api-docs-agent.yml into the repository."""
    repo = GitRepo(repo_path)
    target = repo.root / ".api-docs-agent.yml"
    if target.exists() and not force:
        console.print(f"[yellow]{target} already exists. Use --force to overwrite.[/yellow]")
        raise typer.Exit(EXIT_FAILED)
    target.write_text(_STARTER_CONFIG, encoding="utf-8")
    console.print(f"[green]Wrote {target}[/green]")
    console.print("Next: edit docs.sources to point at your documentation files.")


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


_STARTER_CONFIG = """\
# api-docs-agent configuration
# Environment variables are interpolated as ${VAR} or ${VAR:-default}.

llm:
  # Any registered provider: run `api-docs-agent providers` to list them.
  provider: anthropic
  model: claude-opus-5
  api_key: ${ANTHROPIC_API_KEY}

  # Self-hosted or third-party model instead:
  # provider: openai
  # model: deepseek-chat
  # base_url: https://api.deepseek.com/v1
  # api_key: ${DEEPSEEK_API_KEY}

docs:
  sources:
    - path: docs/openapi.yaml
      type: openapi
      description: The public REST API specification.
    # - path: docs/api.md
    #   type: markdown
    #   description: Human-facing API guide.
  style_guide: |
    Write descriptions in the imperative mood. Do not document internal endpoints.

detect:
  watch_paths:
    - "src/**/*.py"
  ignore_paths:
    - "**/tests/**"

github:
  token: ${GITHUB_TOKEN}
  # repository: owner/name    # inferred from the git remote when omitted
  branch_prefix: docs/api-sync
  labels: [documentation, automated]

notifiers:
  - type: console
  # - type: feishu
  #   options:
  #     webhook_url: ${FEISHU_WEBHOOK_URL}
  #     secret: ${FEISHU_WEBHOOK_SECRET}
  # - type: email
  #   options:
  #     host: smtp.example.com
  #     port: 587
  #     from: api-docs-agent@example.com
  #     username: ${SMTP_USERNAME}
  #     password: ${SMTP_PASSWORD}
  #     notify_commit_author: true
"""


if __name__ == "__main__":  # pragma: no cover
    app()
