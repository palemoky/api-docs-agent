"""Local git operations.

Implemented with `subprocess` rather than a binding: the agent runs in CI where
git is always present, and shelling out keeps the dependency surface (and the
audit surface) small.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..errors import GitError
from ..models import ChangeKind, CodeChange, FileDiff

_STATUS_MAP = {
    "A": ChangeKind.ADDED,
    "M": ChangeKind.MODIFIED,
    "D": ChangeKind.DELETED,
    "R": ChangeKind.RENAMED,
    "C": ChangeKind.ADDED,
    "T": ChangeKind.MODIFIED,
}


class GitRepo:
    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root).resolve()
        if not (self.root / ".git").exists():
            found = self._run("rev-parse", "--show-toplevel", cwd=self.root)
            self.root = Path(found)

    # ----------------------------------------------------------------- run --
    def _run(self, *args: str, cwd: Path | None = None, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.root),
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    # ---------------------------------------------------------------- read --
    def resolve(self, ref: str) -> str:
        return self._run("rev-parse", ref)

    def default_base_ref(self, head: str = "HEAD") -> str:
        """The commit to compare against when the caller gave us only a head.

        Falls back to the empty-tree hash for a repository's first commit so the
        initial run sees every file instead of crashing.
        """
        try:
            return self._run("rev-parse", f"{head}^")
        except GitError:
            return self._run("hash-object", "-t", "tree", "/dev/null")

    def read_file(self, path: str, ref: str | None = None) -> str:
        if ref is None:
            target = self.root / path
            return target.read_text(encoding="utf-8") if target.is_file() else ""
        try:
            return self._run("show", f"{ref}:{path}")
        except GitError:
            return ""

    def collect_changes(self, base_ref: str, head_ref: str = "HEAD") -> CodeChange:
        head_sha = self.resolve(head_ref)
        meta = self._run("show", "-s", "--format=%an%x00%ae%x00%s", head_sha).split("\x00")
        author_name, author_email, subject = (meta + ["", "", ""])[:3]

        numstat = self._run("diff", "--numstat", base_ref, head_ref)
        stats = _parse_numstat(numstat)

        files: list[FileDiff] = []
        name_status = self._run("diff", "--name-status", "-M", base_ref, head_ref)
        for line in name_status.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status, kind = parts[0], _STATUS_MAP.get(parts[0][0], ChangeKind.MODIFIED)
            if status.startswith("R") and len(parts) >= 3:
                old_path, path = parts[1], parts[2]
            else:
                old_path, path = None, parts[1]
            additions, deletions = stats.get(path, (0, 0))
            files.append(
                FileDiff(
                    path=path,
                    kind=kind,
                    old_path=old_path,
                    additions=additions,
                    deletions=deletions,
                )
            )
        return CodeChange(
            base_ref=base_ref,
            head_ref=head_ref,
            commit_sha=head_sha,
            author_name=author_name,
            author_email=author_email,
            subject=subject,
            files=files,
        )

    def patch_for(self, base_ref: str, head_ref: str, path: str) -> str:
        return self._run("diff", base_ref, head_ref, "--", path, check=False)

    # --------------------------------------------------------------- write --
    def current_branch(self) -> str:
        return self._run("rev-parse", "--abbrev-ref", "HEAD")

    def create_branch(self, name: str, from_ref: str) -> None:
        self._run("checkout", "-B", name, self.resolve_start_point(from_ref))

    def resolve_start_point(self, branch: str) -> str:
        """Pick a commit to branch from.

        CI checkouts are usually shallow and detached, so the local branch name
        often does not exist even though `origin/<branch>` does.
        """
        for candidate in (f"origin/{branch}", branch):
            try:
                return self.resolve(candidate)
            except GitError:
                continue
        return self.resolve("HEAD")

    def restore_branch(self, name: str) -> None:
        """Return to where we were, best effort - used in a `finally` block."""
        if name and name != "HEAD":
            self._run("checkout", name, check=False)

    def write_and_commit(
        self, files: dict[str, str], message: str, author: str | None = None
    ) -> str:
        for rel_path, content in files.items():
            target = self.root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self._run("add", "--", rel_path)
        args = ["commit", "-m", message]
        if author:
            args += ["--author", author]
        self._run(*args)
        return self.resolve("HEAD")

    def push(self, branch: str, remote: str = "origin", force: bool = True) -> None:
        args = ["push", remote, f"HEAD:refs/heads/{branch}"]
        if force:
            args.insert(1, "--force-with-lease")
        self._run(*args)

    def remote_url(self, remote: str = "origin") -> str:
        return self._run("remote", "get-url", remote, check=False)


def _parse_numstat(output: str) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        stats[path] = (
            int(added) if added.isdigit() else 0,
            int(removed) if removed.isdigit() else 0,
        )
    return stats
