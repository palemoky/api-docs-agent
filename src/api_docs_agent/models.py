"""Domain models shared across the pipeline.

These are deliberately transport-agnostic: the git layer, the LLM layer, the doc
adapters and the notifiers all speak in terms of these objects, never in terms of
each other's data structures.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ChangeKind(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class FileDiff(BaseModel):
    """A single file's change within a commit range."""

    path: str
    kind: ChangeKind
    old_path: str | None = None
    patch: str = ""
    additions: int = 0
    deletions: int = 0

    @property
    def is_deletion(self) -> bool:
        return self.kind is ChangeKind.DELETED


class CodeChange(BaseModel):
    """Everything the agent knows about the commit range under inspection."""

    base_ref: str
    head_ref: str
    commit_sha: str
    author_name: str = ""
    author_email: str = ""
    subject: str = ""
    files: list[FileDiff] = Field(default_factory=list)

    def total_patch(self, max_chars: int) -> str:
        """Concatenated patch text, truncated to a character budget.

        Truncation is explicit and visible to the model rather than silent, so the
        model can say "I could not see the whole diff" instead of hallucinating.
        """
        chunks: list[str] = []
        used = 0
        for f in self.files:
            header = f"--- {f.path} ({f.kind.value}) ---\n"
            body = f.patch or "(no textual patch)\n"
            piece = header + body
            if used + len(piece) > max_chars:
                remaining = max_chars - used
                if remaining > len(header) + 200:
                    chunks.append(piece[:remaining])
                    used = max_chars
                chunks.append(
                    f"\n[TRUNCATED: {len(self.files) - len(chunks)} more changed files "
                    f"omitted because the diff exceeded the {max_chars} character budget]\n"
                )
                break
            chunks.append(piece)
            used += len(piece)
        return "".join(chunks)


class RelevanceSignal(BaseModel):
    """Why the cheap pre-filter thought a file might touch the public API."""

    path: str
    reasons: list[str] = Field(default_factory=list)


class PreFilterResult(BaseModel):
    relevant_files: list[FileDiff] = Field(default_factory=list)
    signals: list[RelevanceSignal] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list)

    @property
    def has_candidates(self) -> bool:
        return bool(self.relevant_files)


class EndpointChange(BaseModel):
    """One API-surface change the model identified."""

    method: str = ""
    path: str = ""
    change_type: str = ""  # added | removed | request_changed | response_changed | ...
    description: str = ""


class Analysis(BaseModel):
    """Stage 1 LLM output: does the documentation need to change at all?"""

    needs_update: bool
    confidence: float = 0.0
    summary: str = ""
    endpoint_changes: list[EndpointChange] = Field(default_factory=list)
    affected_doc_files: list[str] = Field(default_factory=list)
    reasoning: str = ""


class DocProposal(BaseModel):
    """Stage 2 LLM output for a single documentation file."""

    doc_path: str
    original_content: str
    proposed_content: str
    change_note: str = ""
    validation_errors: list[str] = Field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return self.original_content.strip() == self.proposed_content.strip()

    @property
    def is_valid(self) -> bool:
        return not self.validation_errors


class PullRequest(BaseModel):
    number: int
    url: str
    branch: str


class RunStatus(str, Enum):
    NO_CODE_CHANGES = "no_code_changes"
    NOT_API_RELEVANT = "not_api_relevant"
    DOCS_UP_TO_DATE = "docs_up_to_date"
    PROPOSED = "proposed"
    DRY_RUN = "dry_run"
    FAILED = "failed"


class RunResult(BaseModel):
    status: RunStatus
    change: CodeChange | None = None
    analysis: Analysis | None = None
    proposals: list[DocProposal] = Field(default_factory=list)
    pull_request: PullRequest | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is not RunStatus.FAILED
