"""The cheap pre-filter.

Most commits do not touch the HTTP surface. Sending every diff to a model is slow
and expensive, so a deterministic pass runs first: glob matching to decide which
files are even candidates, then regex signals over the *added and removed lines
only* to decide whether the change smells like an API change.

This stage is intentionally over-inclusive - it decides what the model looks at,
not what the answer is.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch

from .config import DetectConfig
from .models import CodeChange, FileDiff, PreFilterResult, RelevanceSignal
from .vcs.git import GitRepo


def prefilter(change: CodeChange, config: DetectConfig, repo: GitRepo) -> PreFilterResult:
    patterns = [re.compile(p) for p in config.api_signals]
    result = PreFilterResult()

    for file in change.files:
        if not _is_watched(file.path, config):
            result.skipped_files.append(file.path)
            continue

        file.patch = repo.patch_for(change.base_ref, change.head_ref, file.path)
        reasons = _signal_reasons(file, patterns)

        if reasons or config.always_ask_llm:
            result.relevant_files.append(file)
            result.signals.append(RelevanceSignal(path=file.path, reasons=reasons))
        else:
            result.skipped_files.append(file.path)

    return result


def _is_watched(path: str, config: DetectConfig) -> bool:
    if any(glob_match(path, pattern) for pattern in config.ignore_paths):
        return False
    return any(glob_match(path, pattern) for pattern in config.watch_paths)


def glob_match(path: str, pattern: str) -> bool:
    """`fnmatch` with the `**/` prefix people actually expect.

    `fnmatch` never anchors, so `**/tests/**` fails to match `tests/a.py` - there
    is nothing for the leading `**/` to consume. Everyone writes the pattern that
    way anyway, so treat the prefix as "zero or more leading directories".
    """
    if fnmatch(path, pattern):
        return True
    return pattern.startswith("**/") and fnmatch(path, pattern[3:])


def _signal_reasons(file: FileDiff, patterns: list[re.Pattern[str]]) -> list[str]:
    """Match signals against changed lines only, not the whole file."""
    changed_lines = [
        line[1:]
        for line in file.patch.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    if not changed_lines and file.kind.value in {"added", "deleted"}:
        return [f"file was {file.kind.value}"]

    body = "\n".join(changed_lines)
    reasons: list[str] = []
    for pattern in patterns:
        match = pattern.search(body)
        if match:
            reasons.append(f"matched {pattern.pattern!r} at {match.group(0)!r}")
    return reasons
