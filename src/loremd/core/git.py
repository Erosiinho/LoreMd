"""Enveloppe autour du binaire git.

Le CLI est préféré à une liaison native : il gère déjà le rebase, les hooks et
l'authentification du remote exactement comme le feront les humains.
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
    """Un dépôt git local, c'est-à-dire un espace."""

    def __init__(self, path: Path, *, default_branch: str = "main") -> None:
        self.path = Path(path)
        self.default_branch = default_branch

    # -- plomberie -----------------------------------------------------------

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
                f"git {' '.join(args)} a échoué ({result.returncode}) : "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout.strip()

    # -- cycle de vie --------------------------------------------------------

    @property
    def exists(self) -> bool:
        return (self.path / ".git").exists()

    def init(self) -> None:
        """Crée le dépôt s'il n'existe pas déjà. Idempotent."""
        if self.exists:
            return
        self.path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-b", self.default_branch, str(self.path)],
            capture_output=True,
            text=True,
            check=True,
        )
        # Les commits portent le nom de l'agent ; ces valeurs ne servent que de
        # filet quand un outil git externe écrit dans le dépôt.
        self.run("config", "user.name", "LoreMd")
        self.run("config", "user.email", "loremd@localhost")

    def set_remote(self, url: str, name: str = "origin") -> None:
        existing = self.run("remote", check=False)
        if name in existing.split():
            self.run("remote", "set-url", name, url)
        else:
            self.run("remote", "add", name, url)

    # -- écriture ------------------------------------------------------------

    def commit(
        self,
        paths: list[str],
        message: str,
        *,
        author_name: str,
        author_email: str,
    ) -> str | None:
        """Ajoute les chemins et commite. Retourne le sha, ou None si rien à commiter."""
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

    # -- lecture -------------------------------------------------------------

    def history(self, relative_path: str, limit: int = 50) -> list[Commit]:
        """Historique d'un fichier, en suivant les renommages."""
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
        """Fichiers modifiés depuis un commit, sous forme (statut, chemin).

        Sans commit de départ, retourne tous les fichiers suivis en 'A'.
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

    # -- synchronisation (phase 2) -------------------------------------------

    def pull_rebase(self, remote: str = "origin") -> None:
        self.run("pull", "--rebase", remote, self.default_branch)

    def push(self, remote: str = "origin") -> None:
        self.run("push", remote, self.default_branch)

    def has_remote(self, name: str = "origin") -> bool:
        return name in self.run("remote", check=False).split()
