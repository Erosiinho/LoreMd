"""Gestion des espaces : un espace = un dossier = un dépôt git."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..auth.principal import Principal
from ..config import Settings
from ..db import Database
from ..db.repositories import SpaceRepository
from ..errors import NotFound, ValidationError
from .git import GitRepo
from .models import MEMORY_DIRECTORIES, Space

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
CONFIG_DIRNAME = ".loremd"
CONFIG_FILENAME = "space.yaml"

_README = """# Espace « {slug} »

Mémoire partagée gérée par LoreMd. Chaque fichier `.md` est une mémoire, et
chaque modification est un commit.

- `decisions/` — ce qui a été décidé, et pourquoi
- `preferences/` — habitudes et goûts de l'équipe
- `rules/` — contraintes à respecter
- `facts/` — faits stables sur le projet
- `notes/` — le reste

Ce dossier s'ouvre tel quel dans Obsidian. Les modifications poussées sur le
remote sont réindexées par LoreMd.
"""


@dataclass(frozen=True)
class ResolvedSpace:
    """Un espace et son dépôt, après vérification des droits du token."""

    space: Space
    repo: GitRepo

    @property
    def slug(self) -> str:
        return self.space.slug

    @property
    def path(self) -> Path:
        return Path(self.space.path)


def validate_slug(slug: str) -> str:
    if not SLUG_PATTERN.match(slug):
        raise ValidationError(
            "slug d'espace invalide : minuscules, chiffres et tirets, 63 caractères max"
        )
    return slug


class SpaceService:
    """Création, résolution et configuration des espaces."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self._db = database
        self._settings = settings

    def path_for(self, slug: str) -> Path:
        return Path(self._settings.spaces.root) / slug

    def repo_for(self, space: Space) -> GitRepo:
        return GitRepo(Path(space.path), default_branch=self._settings.spaces.default_branch)

    async def create(
        self,
        slug: str,
        *,
        description: str | None = None,
        remote_url: str | None = None,
        review_required: bool = False,
    ) -> Space:
        """Crée le dépôt, l'arborescence et la ligne d'index. Idempotent."""
        validate_slug(slug)
        path = self.path_for(slug)
        repo = GitRepo(path, default_branch=self._settings.spaces.default_branch)
        repo.init()

        for directory in MEMORY_DIRECTORIES:
            (path / directory).mkdir(parents=True, exist_ok=True)
            gitkeep = path / directory / ".gitkeep"
            if not any(p for p in (path / directory).iterdir() if p.name != ".gitkeep"):
                gitkeep.touch()

        readme = path / "README.md"
        if not readme.exists():
            readme.write_text(_README.format(slug=slug), encoding="utf-8")

        self.write_space_config(
            path, description=description, review_required=review_required
        )
        gitignore = path / CONFIG_DIRNAME / ".gitignore"
        gitignore.parent.mkdir(parents=True, exist_ok=True)
        gitignore.write_text("write.lock\n", encoding="utf-8")

        repo.commit(
            ["."],
            f"chore: initialisation de l'espace {slug}",
            author_name="loremd",
            author_email=f"loremd@{self._settings.git.author_email_domain}",
        )
        if remote_url:
            repo.set_remote(remote_url)

        async with self._db.acquire() as connection:
            space = await SpaceRepository(connection).create(
                slug,
                str(path),
                description=description,
                remote_url=remote_url,
                review_required=review_required,
            )
        return space

    def write_space_config(
        self, path: Path, *, description: str | None, review_required: bool
    ) -> None:
        """La config de l'espace vit dans le dépôt : les fichiers sont la vérité."""
        config_path = path / CONFIG_DIRNAME / CONFIG_FILENAME
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            yaml.safe_dump(
                {"description": description, "review_required": review_required},
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

    def read_space_config(self, path: Path) -> dict:
        config_path = path / CONFIG_DIRNAME / CONFIG_FILENAME
        if not config_path.exists():
            return {}
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    async def get(self, slug: str) -> Space:
        async with self._db.acquire() as connection:
            space = await SpaceRepository(connection).get_by_slug(slug)
        if space is None:
            raise NotFound(f"espace inconnu : {slug}")
        return space

    async def resolve(self, slug: str, principal: Principal) -> ResolvedSpace:
        """Récupère l'espace en vérifiant que le token y a droit."""
        space = await self.get(slug)
        principal.require_space(space.id, space.slug)
        return ResolvedSpace(space=space, repo=self.repo_for(space))

    async def list(self, principal: Principal | None = None) -> list[Space]:
        async with self._db.acquire() as connection:
            spaces = await SpaceRepository(connection).list()
        if principal is None or principal.space_ids is None:
            return spaces
        return [space for space in spaces if space.id in principal.space_ids]

    async def accessible_ids(self, principal: Principal) -> list[Space]:
        return await self.list(principal)

    async def set_last_indexed_commit(self, space: Space, commit: str | None) -> None:
        async with self._db.acquire() as connection:
            await SpaceRepository(connection).set_last_indexed_commit(space.id, commit)
