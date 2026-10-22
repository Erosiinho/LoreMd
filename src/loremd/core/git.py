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

#: A server has nobody to type a password: fail fast instead of hanging.
NETWORK_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", "ssh -o BatchMode=yes"),
}


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

    def _exec(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.path,
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, **env} if env else None,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {' '.join(args)} timed out after {timeout}s") from exc

    def run(
        self,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> str:
        result = self._exec(*args, env=env, timeout=timeout)
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
        self._configure()

    def _configure(self) -> None:
        # Commits carry the agent's name; these values are only a fallback for
        # when an external git tool writes to the repository.
        self.run("config", "user.name", "LoreMd")
        self.run("config", "user.email", "loremd@localhost")

    def clone(self, url: str, *, timeout: float | None = None) -> None:
        """Clones an existing remote into this (absent or empty) directory.

        A company that already keeps its memory in a repository gets it as is;
        an empty remote yields an empty repository on the default branch.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", url, str(self.path)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **NETWORK_ENV},
            timeout=timeout,
        )
        if result.returncode != 0:
            raise GitError(f"git clone {url} failed: {result.stderr.strip()}")
        self._configure()
        if self.head() is None:
            self.run("symbolic-ref", "HEAD", f"refs/heads/{self.default_branch}")
            return
        if self.current_branch() != self.default_branch:
            if not self.run("rev-parse", "--verify", "--quiet", self.remote_ref(), check=False):
                raise GitError(
                    f"the remote has no {self.default_branch} branch: set "
                    "spaces.default_branch to the branch it uses"
                )
            self.run("checkout", self.default_branch)

    def current_branch(self) -> str:
        return self.run("symbolic-ref", "--short", "HEAD", check=False)

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
        # Plain `rev-parse HEAD` echoes "HEAD" on a branch with no commit yet.
        sha = self.run("rev-parse", "--verify", "--quiet", "HEAD", check=False)
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

    # -- synchronization ----------------------------------------------------

    def has_remote(self, name: str = "origin") -> bool:
        return name in self.run("remote", check=False).split()

    def remote_ref(self, remote: str = "origin") -> str:
        return f"refs/remotes/{remote}/{self.default_branch}"

    def fetch(self, remote: str = "origin", *, timeout: float | None = None) -> bool:
        """Updates the remote-tracking branch. False when the remote branch does not exist yet."""
        branch = self.default_branch
        result = self._exec(
            "fetch",
            remote,
            f"+refs/heads/{branch}:{self.remote_ref(remote)}",
            env=NETWORK_ENV,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True
        if "couldn't find remote ref" in result.stderr.lower():
            return False  # an empty remote, waiting for its first push
        raise GitError(f"git fetch {remote} failed: {result.stderr.strip()}")

    def ahead_behind(self, ref: str) -> tuple[int, int]:
        """Commits only on HEAD, and only on `ref`."""
        if self.head() is None:
            behind = self.run("rev-list", "--count", ref, check=False)
            return 0, int(behind or 0)
        output = self.run("rev-list", "--left-right", "--count", f"HEAD...{ref}")
        ahead, behind = output.split()
        return int(ahead), int(behind)

    def fast_forward(self, ref: str) -> None:
        if self.head() is None:
            # Nothing local yet: take the remote branch as is.
            self.run("reset", "--hard", ref)
            return
        self.run("merge", "--ff-only", ref)

    def rebase(self, ref: str, *, env: dict[str, str] | None = None) -> bool:
        """Replays local commits on top of `ref`. On conflict, aborts and returns False."""
        result = self._exec("rebase", ref, env=env)
        if result.returncode == 0:
            return True
        self._exec("rebase", "--abort")
        return False

    def merge_no_commit(self, ref: str, *, env: dict[str, str] | None = None) -> bool:
        """Starts a merge without committing it. False when paths are left in conflict."""
        result = self._exec(
            "merge", "--no-ff", "--no-commit", "--allow-unrelated-histories", ref, env=env
        )
        if result.returncode == 0:
            return True
        if self.unmerged_paths():
            return False
        self._exec("merge", "--abort")
        raise GitError(f"git merge {ref} failed: {result.stderr.strip() or result.stdout.strip()}")

    def abort_merge(self) -> None:
        self._exec("merge", "--abort")

    def unmerged_paths(self) -> list[str]:
        output = self.run("diff", "--name-only", "--diff-filter=U", check=False)
        return sorted({line for line in output.splitlines() if line})

    def stage_content(self, relative_path: str, stage: int) -> str | None:
        """A conflicted file as one side has it: 2 = ours (local), 3 = theirs (remote)."""
        result = self._exec("show", f":{stage}:{relative_path}")
        return result.stdout if result.returncode == 0 else None

    def stage(self, paths: list[str]) -> None:
        """Marks paths as resolved, including the ones that were deleted."""
        if paths:
            self.run("add", "--all", "--", *paths)

    def commit_staged(self, message: str, *, author_name: str, author_email: str) -> str | None:
        """Commits what is already staged (a merge, typically)."""
        self.run(
            "commit", "--no-edit", "-m", message, env=identity_env(author_name, author_email)
        )
        return self.head()

    def push(self, remote: str = "origin", *, timeout: float | None = None) -> None:
        self.run(
            "push",
            remote,
            f"HEAD:refs/heads/{self.default_branch}",
            env=NETWORK_ENV,
            timeout=timeout,
        )
