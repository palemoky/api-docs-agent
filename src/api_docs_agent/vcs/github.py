"""GitHub pull-request creation.

Raw REST over httpx instead of PyGithub: three endpoints is not worth a heavy
dependency, and it keeps GitHub Enterprise support to a single `api_url` setting.

`ReviewTarget` is the seam for other forges - implementing the same three methods
for GitLab or Gitea does not touch the pipeline.
"""

from __future__ import annotations

import abc
import contextlib
import os
import re

import httpx

from ..config import GitHubConfig
from ..errors import VCSError
from ..models import PullRequest

_REMOTE_PATTERNS = (re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?$"),)


class ReviewTarget(abc.ABC):
    """Where the agent sends its proposal for human review."""

    @abc.abstractmethod
    def default_branch(self) -> str: ...

    @abc.abstractmethod
    def create_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequest: ...

    @abc.abstractmethod
    def find_open_pull_request(self, *, head: str) -> PullRequest | None: ...


class GitHubTarget(ReviewTarget):
    def __init__(self, config: GitHubConfig, remote_url: str = "") -> None:
        self.config = config
        repository = config.repository or parse_repository(remote_url)
        if not repository:
            raise VCSError(
                "Could not determine the GitHub repository. Set github.repository "
                "in the config or the GITHUB_REPOSITORY environment variable."
            )
        self.repository: str = repository
        if not config.token:
            raise VCSError(
                "No GitHub token. Set github.token in the config (it supports "
                "${GITHUB_TOKEN} interpolation)."
            )
        self._client = httpx.Client(
            base_url=config.api_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {config.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "api-docs-agent",
            },
            timeout=30.0,
        )

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = self._client.request(method, f"/repos/{self.repository}{path}", **kwargs)
        if response.status_code >= 400:
            raise VCSError(
                f"GitHub {method} {path} -> {response.status_code}: {response.text[:500]}"
            )
        return response

    def default_branch(self) -> str:
        if self.config.base_branch:
            return self.config.base_branch
        return self._request("GET", "").json()["default_branch"]

    def find_open_pull_request(self, *, head: str) -> PullRequest | None:
        owner = self.repository.split("/")[0]
        response = self._request(
            "GET", "/pulls", params={"state": "open", "head": f"{owner}:{head}"}
        )
        items = response.json()
        if not items:
            return None
        item = items[0]
        return PullRequest(number=item["number"], url=item["html_url"], branch=head)

    def create_pull_request(self, *, head: str, base: str, title: str, body: str) -> PullRequest:
        existing = self.find_open_pull_request(head=head)
        if existing:
            self._request("PATCH", f"/pulls/{existing.number}", json={"title": title, "body": body})
            return existing

        data = self._request(
            "POST",
            "/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": self.config.draft,
            },
        ).json()
        pr = PullRequest(number=data["number"], url=data["html_url"], branch=head)
        self._decorate(pr)
        return pr

    def _decorate(self, pr: PullRequest) -> None:
        """Labels and reviewers are nice-to-have; never fail the run over them."""
        if self.config.labels:
            with contextlib.suppress(VCSError):
                self._request(
                    "POST", f"/issues/{pr.number}/labels", json={"labels": self.config.labels}
                )
        if self.config.reviewers:
            with contextlib.suppress(VCSError):
                self._request(
                    "POST",
                    f"/pulls/{pr.number}/requested_reviewers",
                    json={"reviewers": self.config.reviewers},
                )


def parse_repository(remote_url: str) -> str | None:
    """Extract `owner/name` from a git remote URL (ssh or https)."""
    from_env = os.environ.get("GITHUB_REPOSITORY")
    if from_env:
        return from_env
    for pattern in _REMOTE_PATTERNS:
        match = pattern.search(remote_url.strip())
        if match:
            return f"{match.group('owner')}/{match.group('name')}"
    return None
