from __future__ import annotations

import httpx
import pytest

from api_docs_agent.config import GitHubConfig
from api_docs_agent.errors import VCSError
from api_docs_agent.vcs import GitHubTarget, parse_repository


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:acme/demo.git", "acme/demo"),
        ("https://github.com/acme/demo.git", "acme/demo"),
        ("https://github.com/acme/demo", "acme/demo"),
        ("git@gitlab.com:acme/demo.git", None),
        ("", None),
    ],
)
def test_repository_is_parsed_from_the_remote(
    remote: str, expected: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert parse_repository(remote) == expected


def _target(handler, **overrides) -> GitHubTarget:
    config = GitHubConfig(repository="acme/demo", token="t", **overrides)
    target = GitHubTarget(config)
    target._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.github.com"
    )
    return target


def test_missing_token_fails_with_an_actionable_message() -> None:
    with pytest.raises(VCSError, match="token"):
        GitHubTarget(GitHubConfig(repository="acme/demo"))


def test_missing_repository_fails_with_an_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with pytest.raises(VCSError, match="repository"):
        GitHubTarget(GitHubConfig(token="t"), remote_url="")


def test_create_pull_request_labels_and_returns_it() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/pulls"):
            return httpx.Response(201, json={"number": 12, "html_url": "https://x/12"})
        return httpx.Response(200, json={})

    pr = _target(handler).create_pull_request(head="docs/x", base="main", title="t", body="b")

    assert (pr.number, pr.url) == (12, "https://x/12")
    assert "POST /repos/acme/demo/issues/12/labels" in calls


def test_an_existing_pull_request_is_updated_rather_than_duplicated() -> None:
    """Re-running the agent on the same branch must not spam the review queue."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return httpx.Response(200, json=[{"number": 5, "html_url": "https://x/5"}])
        return httpx.Response(200, json={})

    pr = _target(handler).create_pull_request(head="docs/x", base="main", title="t", body="b")

    assert pr.number == 5
    assert "PATCH /repos/acme/demo/pulls/5" in calls
    assert not any(c == "POST /repos/acme/demo/pulls" for c in calls)


def test_a_decoration_failure_does_not_lose_the_pull_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/labels"):
            return httpx.Response(403, json={"message": "forbidden"})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"number": 9, "html_url": "https://x/9"})

    assert (
        _target(handler).create_pull_request(head="docs/x", base="main", title="t", body="b").number
        == 9
    )


def test_api_errors_surface_as_vcs_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Validation failed"})

    with pytest.raises(VCSError, match="422"):
        _target(handler).default_branch()
