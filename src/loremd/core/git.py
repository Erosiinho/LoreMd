"""Wrapper around the git binary.

The CLI is preferred over native bindings: it already handles rebase, hooks and
remote authentication exactly the way humans will.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..errors import GitError


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    email: str
    date: datetime
    message: str


_LOG_FORMAT = "%H%x1f%an%x1f%ae%x1f%aI%x1f%s"


class GitRepo:
    """A local git repository, i.e. a space."""

    def __init__(self, path: Path, *, default_branch: str = "main") -> None:
        self.path = Path(path)
        self.default_branch = default_branch

    # -- plumbing ------------------------------------------------------------

    def run(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout.strip()

    # -- lifecycle -----------------------------------------------------------

    @property
    def exists(self) -> bool:
        return (self.path / ".git").exists()

    def init(self) -> None:
        """Creates the repository if it does not exist yet. Idempotent."""
        if self.exists:
            return
        self.path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-b", self.default_branch, str(self.path)],
            capture_output=True,
            text=True,
            check=True,
        )
        # Commits carry the agent's name; these values are only a fallback for
        # when an external git tool writes to the repository.
        self.run("config", "user.name", "LoreMd")
        self.run("config", "user.email", "loremd@localhost")

    def set_remote(self, url: str, name: str = "origin") -> None:
        existing = self.run("remote", check=False)
        if name in existing.split():
            self.run("remote", "set-url", name, url)
        else:
            self.run("remote", "add", name, url)

    # -- writing -------------------------------------------------------------

    def commit(
        self,
        paths: list[str],
        message: str,
        *,
        author_name: str,
        author_email: str,
    ) -> str | None:
        """Stages the paths and commits. Returns the sha, or None if there is nothing to commit."""
        self.run("add", "--", *paths)
        if not self.run("diff", "--cached", "--name-only"):
            return None
        self.run(
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-m",
            message,
        )
        return self.head()

    def head(self) -> str | None:
        sha = self.run("rev-parse", "HEAD", check=False)
        return sha or None

    # -- reading -------------------------------------------------------------

    def history(self, relative_path: str, limit: int = 50) -> list[Commit]:
        """History of a file, following renames."""
        output = self.run(
            "log",
            f"--max-count={limit}",
            f"--format={_LOG_FORMAT}",
            "--follow",
            "--",
            relative_path,
            check=False,
        )
        commits: list[Commit] = []
        for line in output.splitlines():
            sha, author, email, date, message = line.split("\x1f")
            commits.append(
                Commit(
                    sha=sha,
                    author=author,
                    email=email,
                    date=datetime.fromisoformat(date),
                    message=message,
                )
            )
        return commits

    def changed_files(self, since_commit: str | None) -> list[tuple[str, str]]:
        """Files changed since a commit, as (status, path).

        Without a starting commit, returns every tracked file as 'A'.
        """
        if since_commit is None:
            return [("A", path) for path in self.run("ls-files").splitlines() if path]
        output = self.run(
            "diff", "--name-status", "--no-renames", f"{since_commit}..HEAD", check=False
        )
        changes: list[tuple[str, str]] = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                changes.append((parts[0][:1], parts[-1]))
        return changes

    def file_at(self, relative_path: str, revision: str) -> str | None:
        content = self.run("show", f"{revision}:{relative_path}", check=False)
        return content or None

    # -- synchronization (phase 2) -------------------------------------------

    def pull_rebase(self, remote: str = "origin") -> None:
        self.run("pull", "--rebase", remote, self.default_branch)

    def push(self, remote: str = "origin") -> None:
        self.run("push", remote, self.default_branch)

    def has_remote(self, name: str = "origin") -> bool:
        return name in self.run("remote", check=False).split()
