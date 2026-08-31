from __future__ import annotations

from api_docs_agent.config import NotifierConfig
from api_docs_agent.models import (
    Analysis,
    CodeChange,
    DocProposal,
    PullRequest,
    RunResult,
    RunStatus,
)
from api_docs_agent.notify import Notifier, build_notifiers, dispatch, register_notifier


def _result(status: RunStatus = RunStatus.PROPOSED) -> RunResult:
    return RunResult(
        status=status,
        change=CodeChange(
            base_ref="a",
            head_ref="b",
            commit_sha="deadbeefcafe",
            author_name="Rui",
            author_email="rui@example.com",
            subject="add delete endpoint",
        ),
        analysis=Analysis(needs_update=True, confidence=0.9, summary="DELETE was added."),
        proposals=[
            DocProposal(doc_path="docs/openapi.yaml", original_content="a", proposed_content="b")
        ],
        pull_request=PullRequest(number=7, url="https://example.invalid/pr/7", branch="docs/x"),
    )


@register_notifier
class RecordingNotifier(Notifier):
    name = "recording"
    seen: list[RunResult] = []

    def send(self, result: RunResult) -> None:
        RecordingNotifier.seen.append(result)


@register_notifier
class ExplodingNotifier(Notifier):
    name = "exploding"

    def send(self, result: RunResult) -> None:
        raise RuntimeError("the webhook is down")


def test_a_failing_notifier_never_breaks_the_run() -> None:
    """A PR was already opened; a dead webhook must not turn that into a failure."""
    RecordingNotifier.seen.clear()
    notifiers = build_notifiers(
        [NotifierConfig(type="exploding"), NotifierConfig(type="recording")]
    )

    dispatch(notifiers, _result())  # must not raise

    assert len(RecordingNotifier.seen) == 1


def test_disabled_and_unknown_notifiers_are_dropped() -> None:
    built = build_notifiers(
        [
            NotifierConfig(type="recording", enabled=False),
            NotifierConfig(type="does-not-exist"),
            NotifierConfig(type="console"),
        ]
    )
    assert [n.name for n in built] == ["console"]


def test_quiet_statuses_are_not_announced_by_default() -> None:
    RecordingNotifier.seen.clear()
    notifiers = build_notifiers([NotifierConfig(type="recording")])

    dispatch(notifiers, _result(RunStatus.DOCS_UP_TO_DATE))
    assert RecordingNotifier.seen == []

    dispatch(notifiers, _result(RunStatus.PROPOSED))
    assert len(RecordingNotifier.seen) == 1


def test_notify_on_is_configurable() -> None:
    RecordingNotifier.seen.clear()
    notifiers = build_notifiers(
        [NotifierConfig(type="recording", options={"notify_on": ["docs_up_to_date"]})]
    )
    dispatch(notifiers, _result(RunStatus.DOCS_UP_TO_DATE))
    assert len(RecordingNotifier.seen) == 1


def test_summary_carries_what_a_reviewer_needs() -> None:
    from api_docs_agent.notify import render_summary

    text = render_summary(_result())
    assert "https://example.invalid/pr/7" in text
    assert "rui@example.com" in text
    assert "DELETE was added." in text
    assert "docs/openapi.yaml" in text
