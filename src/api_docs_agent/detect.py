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
from functools import lru_cache

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


@lru_cache(maxsize=512)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob into a regex.

    `fnmatch` is not usable here: its `*` happily crosses `/` (so `src/*.py`
    would match `src/a/b.py`), and its `**/` cannot match zero directories (so
    `src/**/*.py` would miss `src/app.py`) - which is exactly the pattern people
    write in a config file.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")  # zero or more directories
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return re.compile(f"^{''.join(out)}$")


def glob_match(path: str, pattern: str) -> bool:
    return _compile_glob(pattern).match(path) is not None


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
