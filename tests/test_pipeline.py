"""End-to-end pipeline tests with a scripted model and an in-memory forge.

These prove the interesting behaviour without a network call: that a run stops
before spending tokens when nothing is API-relevant, that an invalid generation
is retried with the validation errors, and that a valid one becomes a pull
request.
"""

from __future__ import annotations

from typing import Any

from api_docs_agent.config import AgentConfig, DocSource, GitHubConfig, LLMConfig
from api_docs_agent.llm import LLMProvider
from api_docs_agent.models import PullRequest, RunStatus
from api_docs_agent.pipeline import DocSyncPipeline
from api_docs_agent.vcs import GitRepo, ReviewTarget

from .conftest import git

UPDATED_SPEC = """\
openapi: 3.0.3
info:
  title: T
  version: 1.0.0
paths:
  /users:
    get:
      responses:
        '200':
          description: ok
  /users/{user_id}:
    delete:
      parameters:
        - name: user_id
          in: path
          required: true
          schema:
            type: integer
      responses:
        '204':
          description: The user was deleted.
"""

BROKEN_SPEC = "openapi: 3.0.3\ninfo:\n  title: T\n"


class ScriptedLLM(LLMProvider):
    """Returns pre-baked responses in order and records every prompt it saw."""

    name = "scripted"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(LLMConfig(provider="scripted", model="scripted"))
        self.responses = responses
        self.prompts: list[str] = []

    def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> str:
        raise AssertionError("complete() should not be used by the pipeline")

    def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class FakeForge(ReviewTarget):
    def __init__(self) -> None:
        self.created: list[dict[str, str]] = []

    def default_branch(self) -> str:
        return "main"

    def find_open_pull_request(self, *, head: str) -> PullRequest | None:
        return None

    def create_pull_request(self, *, head: str, base: str, title: str, body: str) -> PullRequest:
        self.created.append({"head": head, "base": base, "title": title, "body": body})
        return PullRequest(number=1, url="https://example.invalid/pr/1", branch=head)


def _config() -> AgentConfig:
    config = AgentConfig()
    config.docs.sources = [DocSource(path="docs/openapi.yaml", type="openapi")]
    config.github = GitHubConfig(repository="acme/demo", token="t")
    config.notifiers = []
    return config


def _add_endpoint(repo: GitRepo) -> None:
    (repo.root / "src" / "app.py").write_text(
        '@app.get("/users")\ndef list_users():\n    return []\n\n'
        '@app.delete("/users/{user_id}")\ndef delete_user(user_id: int):\n    return None\n',
        encoding="utf-8",
    )
    git(repo.root, "add", ".")
    git(repo.root, "commit", "-q", "-m", "add delete endpoint")


ANALYSIS_YES = {
    "needs_update": True,
    "confidence": 0.9,
    "summary": "A DELETE /users/{user_id} endpoint was added.",
    "reasoning": "New @app.delete route in src/app.py.",
    "endpoint_changes": [
        {
            "method": "DELETE",
            "path": "/users/{user_id}",
            "change_type": "added",
            "description": "Deletes a user.",
        }
    ],
    "affected_doc_files": ["docs/openapi.yaml"],
}


def test_no_model_call_when_nothing_is_api_relevant(repo: GitRepo) -> None:
    (repo.root / "README.md").write_text("hello\n", encoding="utf-8")
    git(repo.root, "add", ".")
    git(repo.root, "commit", "-q", "-m", "docs tweak")

    llm = ScriptedLLM([])
    result = DocSyncPipeline(_config(), repo, llm, FakeForge()).run(notify=False)

    assert result.status is RunStatus.NOT_API_RELEVANT
    assert llm.prompts == []  # the pre-filter saved a paid call


def test_analysis_saying_no_stops_before_generation(repo: GitRepo) -> None:
    _add_endpoint(repo)
    llm = ScriptedLLM(
        [
            {
                "needs_update": False,
                "confidence": 0.95,
                "summary": "Internal only.",
                "reasoning": "No contract change.",
                "endpoint_changes": [],
                "affected_doc_files": [],
            }
        ]
    )
    result = DocSyncPipeline(_config(), repo, llm, FakeForge()).run(notify=False)

    assert result.status is RunStatus.DOCS_UP_TO_DATE
    assert len(llm.prompts) == 1


def test_valid_proposal_becomes_a_pull_request(repo: GitRepo) -> None:
    _add_endpoint(repo)
    forge = FakeForge()
    llm = ScriptedLLM(
        [ANALYSIS_YES, {"content": UPDATED_SPEC, "change_note": "Documented DELETE."}]
    )
    result = DocSyncPipeline(_config(), repo, llm, forge).run(notify=False)

    assert result.status is RunStatus.PROPOSED
    assert result.pull_request and result.pull_request.number == 1

    pr = forge.created[0]
    assert pr["base"] == "main"
    assert "DELETE /users/{user_id}" in pr["body"]
    assert "Approve and merge" in pr["body"]

    # The commit really landed on the new branch.
    assert "/users/{user_id}" in repo.read_file("docs/openapi.yaml", ref=pr["head"])


def test_invalid_generation_is_retried_with_the_validation_errors(repo: GitRepo) -> None:
    _add_endpoint(repo)
    llm = ScriptedLLM(
        [
            ANALYSIS_YES,
            {"content": BROKEN_SPEC, "change_note": "oops"},
            {"content": UPDATED_SPEC, "change_note": "fixed"},
        ]
    )
    result = DocSyncPipeline(_config(), repo, llm, FakeForge()).run(notify=False)

    assert result.status is RunStatus.PROPOSED
    assert len(llm.prompts) == 3
    assert "previous attempt was rejected" in llm.prompts[2]
    assert "paths" in llm.prompts[2]


def test_persistently_invalid_generation_never_reaches_a_pull_request(repo: GitRepo) -> None:
    _add_endpoint(repo)
    forge = FakeForge()
    llm = ScriptedLLM([ANALYSIS_YES] + [{"content": BROKEN_SPEC, "change_note": "x"}] * 3)
    result = DocSyncPipeline(_config(), repo, llm, forge).run(notify=False)

    assert result.status is RunStatus.DOCS_UP_TO_DATE
    assert forge.created == []


def test_dry_run_touches_no_branch_and_no_forge(repo: GitRepo) -> None:
    _add_endpoint(repo)
    forge = FakeForge()
    llm = ScriptedLLM([ANALYSIS_YES, {"content": UPDATED_SPEC, "change_note": "n"}])
    result = DocSyncPipeline(_config(), repo, llm, forge).run(dry_run=True, notify=False)

    assert result.status is RunStatus.DRY_RUN
    assert forge.created == []
    assert repo.current_branch() == "main"
    assert "/users/{user_id}" not in repo.read_file("docs/openapi.yaml")


def test_hallucinated_doc_paths_are_dropped(repo: GitRepo) -> None:
    _add_endpoint(repo)
    analysis = dict(ANALYSIS_YES, affected_doc_files=["docs/openapi.yaml", "docs/imaginary.md"])
    llm = ScriptedLLM([analysis, {"content": UPDATED_SPEC, "change_note": "n"}])
    result = DocSyncPipeline(_config(), repo, llm, FakeForge()).run(dry_run=True, notify=False)

    assert [p.doc_path for p in result.proposals] == ["docs/openapi.yaml"]
