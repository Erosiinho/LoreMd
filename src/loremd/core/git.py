"""Wrapper around the git binary.

The CLI is preferred over native bindings: it already handles rebase, hooks and
remote authentication exactly the way humans will.
"""

from __future__ import annotations

import os
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

#: Fallback identity when a caller passes an empty name or email.
FALLBACK_IDENTITY = ("loremd", "loremd@localhost")


def identity_env(author_name: str, author_email: str) -> dict[str, str]:
    """Environment forcing who signs a commit.

    `-c user.name` is not enough: `GIT_AUTHOR_NAME` and friends already present
    in the environment take precedence over any config, so a host that exports
    them would silently sign every agent commit with its own identity. Setting
    these variables explicitly is the only way to be sure the author is the
    agent, which principle 5 of the plan requires.
    """
    name = author_name.strip() or FALLBACK_IDENTITY[0]
    email = author_email.strip() or FALLBACK_IDENTITY[1]
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


class GitRepo:
    """A local git repository, i.e. a space."""

    def __init__(self, path: Path, *, default_branch: str = "main") -> None:
        self.path = Path(path)
        self.default_branch = default_branch

    # -- plumbing ------------------------------------------------------------

    def run(self, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **env} if env else None,
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
        self.run("commit", "-m", message, env=identity_env(author_name, author_email))
        return self.head()

    def head(self) -> str | None:
        sha = self.run("rev-parse", "HEAD", check=False)
        return sha or None

    def has_commit(self, sha: str) -> bool:
        """Whether a commit is still reachable in this repository.

        A rebase or a force-push can make a previously indexed commit vanish;
        the caller then has to fall back to a full reindex instead of diffing
        against nothing.
        """
        if not sha:
            return False
        # --verify --quiet prints the resolved sha, or nothing when it is gone.
        resolved = self.run("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False)
        return bool(resolved)

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
