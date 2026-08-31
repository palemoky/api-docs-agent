"""Glob semantics, because `fnmatch` gets both of these wrong."""

from __future__ import annotations

import pytest

from api_docs_agent.detect import glob_match


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        # `**/` must match zero directories as well as many.
        ("src/app.py", "src/**/*.py", True),
        ("src/api/v1/app.py", "src/**/*.py", True),
        ("tests/test_app.py", "**/tests/**", True),
        ("a/b/tests/test_app.py", "**/tests/**", True),
        ("src/app.py", "**/*.py", True),
        # A single `*` must not cross a directory separator.
        ("src/a/b.py", "src/*.py", False),
        ("src/b.py", "src/*.py", True),
        # Anchoring: a pattern matches the whole path, not a substring.
        ("vendor/src/app.py", "src/**/*.py", False),
        ("src/app.pyc", "src/*.py", False),
        # Literals are escaped, not treated as regex.
        ("src/a.b.py", "src/a.b.py", True),
        ("src/axbxpy", "src/a.b.py", False),
        # `?` matches exactly one non-separator character.
        ("v1/api.py", "v?/api.py", True),
        ("v10/api.py", "v?/api.py", False),
    ],
)
def test_glob_match(path: str, pattern: str, expected: bool) -> None:
    assert glob_match(path, pattern) is expected
