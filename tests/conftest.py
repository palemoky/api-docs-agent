from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from api_docs_agent.vcs import GitRepo


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    """A throwaway git repository with one commit and a local bare `origin`."""
    tmp_path = tmp_path / "work"
    tmp_path.mkdir()
    git(tmp_path.parent, "init", "-q", "--bare", "origin.git")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "remote", "add", "origin", str(tmp_path.parent / "origin.git"))
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        '@app.get("/users")\ndef list_users():\n    return []\n', encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "openapi.yaml").write_text(
        "openapi: 3.0.3\n"
        "info:\n  title: T\n  version: 1.0.0\n"
        "paths:\n  /users:\n    get:\n      responses:\n"
        "        '200':\n          description: ok\n",
        encoding="utf-8",
    )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "initial")
    git(tmp_path, "push", "-q", "-u", "origin", "main")
    return GitRepo(tmp_path)
