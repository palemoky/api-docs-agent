from __future__ import annotations

from api_docs_agent.config import DetectConfig
from api_docs_agent.detect import prefilter
from api_docs_agent.vcs import GitRepo

from .conftest import git


def _commit(repo: GitRepo, path: str, content: str, message: str) -> None:
    target = repo.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo.root, "add", ".")
    git(repo.root, "commit", "-q", "-m", message)


def test_route_change_is_a_candidate(repo: GitRepo) -> None:
    _commit(
        repo,
        "src/app.py",
        '@app.get("/users")\ndef list_users():\n    return []\n\n'
        '@app.delete("/users/{user_id}")\ndef delete_user(user_id: int):\n    return None\n',
        "add delete endpoint",
    )
    change = repo.collect_changes(repo.default_base_ref(), "HEAD")
    result = prefilter(change, DetectConfig(), repo)

    assert [f.path for f in result.relevant_files] == ["src/app.py"]
    assert result.signals[0].reasons


def test_internal_refactor_is_skipped(repo: GitRepo) -> None:
    _commit(
        repo,
        "src/app.py",
        '@app.get("/users")\ndef list_users():\n    users = []\n    return users\n',
        "refactor internals",
    )
    change = repo.collect_changes(repo.default_base_ref(), "HEAD")
    result = prefilter(change, DetectConfig(), repo)

    assert not result.has_candidates
    assert "src/app.py" in result.skipped_files


def test_ignored_paths_never_become_candidates(repo: GitRepo) -> None:
    _commit(
        repo,
        "tests/test_app.py",
        '@app.get("/users")\ndef fake():\n    return []\n',
        "add a test",
    )
    change = repo.collect_changes(repo.default_base_ref(), "HEAD")
    result = prefilter(change, DetectConfig(), repo)

    assert not result.has_candidates


def test_removing_a_route_is_also_a_candidate(repo: GitRepo) -> None:
    """Deleted lines count: removing an endpoint is an API change too."""
    _commit(repo, "src/app.py", "# the endpoint is gone\n", "drop the endpoint")
    change = repo.collect_changes(repo.default_base_ref(), "HEAD")
    result = prefilter(change, DetectConfig(), repo)

    assert [f.path for f in result.relevant_files] == ["src/app.py"]


def test_always_ask_llm_overrides_the_signal_check(repo: GitRepo) -> None:
    _commit(repo, "src/util.py", "def slugify(value):\n    return value.lower()\n", "helper")
    change = repo.collect_changes(repo.default_base_ref(), "HEAD")

    assert not prefilter(change, DetectConfig(), repo).has_candidates
    assert prefilter(change, DetectConfig(always_ask_llm=True), repo).has_candidates
