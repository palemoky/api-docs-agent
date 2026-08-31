"""The orchestrator.

    collect diff
      -> pre-filter (deterministic, free)
      -> analyse    (one LLM call: does anything need to change?)
      -> generate   (one LLM call per affected doc, with a validation retry)
      -> branch + commit + push
      -> open a pull request
      -> notify

Every stage can stop the run early, and every stage's decision ends up in the
`RunResult`, so the CLI, the tests and the PR body all read the same record.
"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime, timezone

from .config import AgentConfig
from .detect import prefilter
from .docs import DocAdapter, get_adapter
from .errors import AgentError
from .llm import LLMProvider, create_llm
from .models import (
    Analysis,
    CodeChange,
    DocProposal,
    PreFilterResult,
    PullRequest,
    RunResult,
    RunStatus,
)
from .notify import build_notifiers, dispatch
from .prompts import (
    ANALYSIS_SCHEMA,
    GENERATION_SCHEMA,
    SYSTEM,
    analysis_prompt,
    generation_prompt,
)
from .vcs import GitHubTarget, GitRepo, ReviewTarget

logger = logging.getLogger(__name__)

#: How many times to hand validation errors back to the model before giving up.
MAX_VALIDATION_RETRIES = 2


class DocSyncPipeline:
    def __init__(
        self,
        config: AgentConfig,
        repo: GitRepo,
        llm: LLMProvider | None = None,
        review_target: ReviewTarget | None = None,
    ) -> None:
        self.config = config
        self.repo = repo
        self.llm = llm or create_llm(config.llm)
        self._review_target = review_target

    # ------------------------------------------------------------------ run --
    def run(
        self,
        *,
        base_ref: str | None = None,
        head_ref: str = "HEAD",
        dry_run: bool = False,
        notify: bool = True,
    ) -> RunResult:
        try:
            result = self._run(base_ref=base_ref, head_ref=head_ref, dry_run=dry_run)
        except AgentError as exc:
            logger.error("Run failed: %s", exc)
            result = RunResult(status=RunStatus.FAILED, message=str(exc))

        if notify:
            dispatch(build_notifiers(self.config.notifiers), result)
        return result

    def _run(self, *, base_ref: str | None, head_ref: str, dry_run: bool) -> RunResult:
        base_ref = base_ref or self.repo.default_base_ref(head_ref)
        change = self.repo.collect_changes(base_ref, head_ref)
        if not change.files:
            return RunResult(
                status=RunStatus.NO_CODE_CHANGES,
                change=change,
                message=f"No file changes between {base_ref} and {head_ref}",
            )

        filtered = prefilter(change, self.config.detect, self.repo)
        if not filtered.has_candidates:
            return RunResult(
                status=RunStatus.NOT_API_RELEVANT,
                change=change,
                message=(
                    f"{len(change.files)} changed file(s), none matched an API signal. "
                    "No model call was made."
                ),
            )

        analysis = self._analyse(change, filtered)
        if not analysis.needs_update:
            return RunResult(
                status=RunStatus.DOCS_UP_TO_DATE,
                change=change,
                analysis=analysis,
                message=analysis.summary,
            )

        proposals = self._generate(change, filtered, analysis)
        proposals = [p for p in proposals if not p.is_noop and p.is_valid]
        if not proposals:
            return RunResult(
                status=RunStatus.DOCS_UP_TO_DATE,
                change=change,
                analysis=analysis,
                message="The model proposed no usable edits to the documentation.",
            )

        if dry_run:
            return RunResult(
                status=RunStatus.DRY_RUN,
                change=change,
                analysis=analysis,
                proposals=proposals,
                message="Dry run: nothing was pushed.",
            )

        pull_request = self._publish(change, analysis, proposals)
        return RunResult(
            status=RunStatus.PROPOSED,
            change=change,
            analysis=analysis,
            proposals=proposals,
            pull_request=pull_request,
        )

    # ------------------------------------------------------------- stage 1 --
    def _analyse(self, change: CodeChange, filtered: PreFilterResult) -> Analysis:
        signals = "\n".join(
            f"- {signal.path}: {'; '.join(signal.reasons) or 'included by configuration'}"
            for signal in filtered.signals
        )
        diff_only = CodeChange(
            base_ref=change.base_ref,
            head_ref=change.head_ref,
            commit_sha=change.commit_sha,
            files=filtered.relevant_files,
        )
        raw = self.llm.complete_json(
            system=SYSTEM,
            prompt=analysis_prompt(
                commit_subject=change.subject,
                signals=signals,
                diff=diff_only.total_patch(self.config.detect.max_diff_chars),
                doc_inventory=self._doc_inventory(),
            ),
            schema=ANALYSIS_SCHEMA,
        )
        analysis = Analysis.model_validate(raw)

        known = {source.path for source in self.config.docs.sources}
        hallucinated = [path for path in analysis.affected_doc_files if path not in known]
        if hallucinated:
            logger.warning("Model named unknown doc files, ignoring: %s", hallucinated)
        analysis.affected_doc_files = [p for p in analysis.affected_doc_files if p in known]
        if analysis.needs_update and not analysis.affected_doc_files:
            # It found a contract change but could not attribute it to a file.
            # Fall back to every configured doc rather than silently doing nothing.
            analysis.affected_doc_files = sorted(known)
        return analysis

    # ------------------------------------------------------------- stage 2 --
    def _generate(
        self, change: CodeChange, filtered: PreFilterResult, analysis: Analysis
    ) -> list[DocProposal]:
        diff_only = CodeChange(
            base_ref=change.base_ref,
            head_ref=change.head_ref,
            commit_sha=change.commit_sha,
            files=filtered.relevant_files,
        )
        diff_text = diff_only.total_patch(self.config.detect.max_diff_chars)
        endpoint_changes = "\n".join(
            f"- {e.method} {e.path} [{e.change_type}] {e.description}"
            for e in analysis.endpoint_changes
        )

        proposals: list[DocProposal] = []
        for doc_path in analysis.affected_doc_files:
            source = next(s for s in self.config.docs.sources if s.path == doc_path)
            adapter = get_adapter(source.type, source.path)
            original = self.repo.read_file(doc_path)
            if not original:
                logger.warning("Documentation file %s is missing or empty; skipping", doc_path)
                continue
            proposals.append(
                self._generate_one(
                    doc_path=doc_path,
                    adapter=adapter,
                    original=original,
                    analysis=analysis,
                    endpoint_changes=endpoint_changes,
                    diff_text=diff_text,
                )
            )
        return proposals

    def _generate_one(
        self,
        *,
        doc_path: str,
        adapter: DocAdapter,
        original: str,
        analysis: Analysis,
        endpoint_changes: str,
        diff_text: str,
    ) -> DocProposal:
        feedback = ""
        proposal = DocProposal(
            doc_path=doc_path, original_content=original, proposed_content=original
        )

        for attempt in range(MAX_VALIDATION_RETRIES + 1):
            raw = self.llm.complete_json(
                system=SYSTEM,
                prompt=generation_prompt(
                    doc_path=doc_path,
                    doc_content=original,
                    guidance=adapter.prompt_guidance(),
                    style_guide=self.config.docs.style_guide,
                    analysis_summary=analysis.summary,
                    endpoint_changes=endpoint_changes,
                    diff=diff_text,
                    validation_feedback=feedback,
                ),
                schema=GENERATION_SCHEMA,
            )
            content = str(raw.get("content", ""))
            errors = adapter.validate(content)
            proposal = DocProposal(
                doc_path=doc_path,
                original_content=original,
                proposed_content=content,
                change_note=str(raw.get("change_note", "")),
                validation_errors=errors,
            )
            if not errors:
                return proposal

            logger.warning(
                "Validation failed for %s (attempt %d/%d): %s",
                doc_path,
                attempt + 1,
                MAX_VALIDATION_RETRIES + 1,
                "; ".join(errors),
            )
            feedback = "\n".join(f"- {error}" for error in errors)

        return proposal

    # ------------------------------------------------------------- stage 3 --
    def _publish(
        self, change: CodeChange, analysis: Analysis, proposals: list[DocProposal]
    ) -> PullRequest:
        target = self.review_target()
        base_branch = target.default_branch()
        branch = (
            f"{self.config.github.branch_prefix}/"
            f"{change.commit_sha[:8]}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        )

        original_branch = self.repo.current_branch()
        try:
            self.repo.create_branch(branch, base_branch)
            self.repo.write_and_commit(
                {p.doc_path: p.proposed_content for p in proposals},
                message=self._commit_message(change, analysis),
            )
            self.repo.push(branch)
        finally:
            self.repo.restore_branch(original_branch)

        return target.create_pull_request(
            head=branch,
            base=base_branch,
            title=f"docs: sync API documentation with {change.commit_sha[:8]}",
            body=self.pr_body(change, analysis, proposals),
        )

    def review_target(self) -> ReviewTarget:
        if self._review_target is None:
            self._review_target = GitHubTarget(self.config.github, self.repo.remote_url())
        return self._review_target

    # ----------------------------------------------------------- rendering --
    def _doc_inventory(self) -> str:
        if not self.config.docs.sources:
            return "(none configured)"
        lines = []
        for source in self.config.docs.sources:
            adapter = get_adapter(source.type, source.path)
            content = self.repo.read_file(source.path)
            outline = adapter.summarize(content) if content else "(file not found)"
            lines.append(
                f"### {source.path} (format: {adapter.name})\n"
                f"{source.description}\n"
                f"Current contents (outline):\n{outline}\n"
            )
        return "\n".join(lines)

    def _commit_message(self, change: CodeChange, analysis: Analysis) -> str:
        return (
            f"docs: sync API documentation with {change.commit_sha[:8]}\n\n"
            f"{analysis.summary}\n\n"
            f"Generated by api-docs-agent using {self.llm.describe()}.\n"
            f"Source commit: {change.commit_sha}"
        )

    def pr_body(
        self, change: CodeChange, analysis: Analysis, proposals: list[DocProposal]
    ) -> str:
        lines = [
            "## Why this pull request exists",
            "",
            analysis.summary,
            "",
            f"Source commit: `{change.commit_sha}` — {change.subject}",
            f"Author: {change.author_name} <{change.author_email}>",
            "",
        ]

        if analysis.confidence < self.config.low_confidence_threshold:
            lines += [
                f"> **Low confidence ({analysis.confidence:.0%}).** "
                "Read the diff carefully before merging.",
                "",
            ]

        if analysis.endpoint_changes:
            lines += ["### API changes detected", ""]
            lines += [
                f"- **{e.method} {e.path}** — `{e.change_type}` — {e.description}"
                for e in analysis.endpoint_changes
            ]
            lines.append("")

        lines += ["### Documentation edits", ""]
        for proposal in proposals:
            lines += [f"**`{proposal.doc_path}`**", "", proposal.change_note or "(no note)", ""]
            diff = "\n".join(
                difflib.unified_diff(
                    proposal.original_content.splitlines(),
                    proposal.proposed_content.splitlines(),
                    fromfile=f"a/{proposal.doc_path}",
                    tofile=f"b/{proposal.doc_path}",
                    lineterm="",
                    n=3,
                )
            )
            if len(diff) < 8000:
                lines += [
                    "<details><summary>Diff</summary>",
                    "",
                    "```diff",
                    diff,
                    "```",
                    "",
                    "</details>",
                    "",
                ]

        lines += [
            "### How to respond",
            "",
            "- **Correct?** Approve and merge.",
            "- **Nearly right?** Edit the files on this branch and merge — "
            "the agent will not overwrite your changes.",
            "- **Wrong?** Close this pull request; nothing else happens.",
            "",
            "---",
            f"<sub>Generated by api-docs-agent · model `{self.llm.describe()}` · "
            f"analysis confidence {analysis.confidence:.0%}</sub>",
        ]
        return "\n".join(lines)
