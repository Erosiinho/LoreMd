"""Lecture et écriture des fichiers `.md` à frontmatter YAML.

Le rendu est déterministe : même ordre de clés, dates ISO 8601, corps terminé
par un retour à la ligne. Deux écritures identiques produisent donc le même
fichier, et git ne voit pas de bruit.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from ..errors import ValidationError
from .models import Frontmatter, Memory

DELIMITER = "---"

#: Ordre d'écriture des clés du frontmatter.
KEY_ORDER = (
    "id",
    "type",
    "title",
    "project",
    "tags",
    "status",
    "verified",
    "source",
    "author",
    "created",
    "updated",
    "supersedes",
)


def split_document(text: str) -> tuple[dict[str, Any], str]:
    """Sépare le frontmatter du corps. Lève si l'en-tête est absent ou invalide."""
    stripped = text.lstrip("﻿")
    if not stripped.startswith(DELIMITER):
        raise ValidationError("frontmatter manquant : le fichier doit commencer par ---")

    lines = stripped.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == DELIMITER:
            header = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            break
    else:
        raise ValidationError("frontmatter non terminé : délimiteur --- de fin absent")

    try:
        data = yaml.safe_load(header) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"frontmatter YAML invalide : {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("le frontmatter doit être un mapping YAML")
    return data, body.strip()


def parse(text: str, *, space: str, path: str) -> Memory:
    """Construit une mémoire validée à partir du contenu d'un fichier."""
    data, body = split_document(text)
    try:
        frontmatter = Frontmatter(**data)
    except PydanticValidationError as exc:
        raise ValidationError(f"frontmatter invalide dans {path} : {exc}") from exc
    return Memory(frontmatter=frontmatter, content=body, space=space, path=path)


def read_file(file_path: Path, *, space: str, relative_path: str) -> Memory:
    return parse(file_path.read_text(encoding="utf-8"), space=space, path=relative_path)


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if hasattr(value, "value"):  # StrEnum
        return value.value
    return value


def render(memory: Memory) -> str:
    """Rend la mémoire au format fichier."""
    dumped = memory.frontmatter.model_dump()
    ordered: dict[str, Any] = {}
    for key in KEY_ORDER:
        if key in dumped:
            ordered[key] = _serialize(dumped.pop(key))
    for key in sorted(dumped):  # champs additionnels conservés tels quels
        ordered[key] = _serialize(dumped[key])

    header = yaml.safe_dump(
        ordered,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    ).rstrip()
    body = memory.content.strip()
    return f"{DELIMITER}\n{header}\n{DELIMITER}\n\n{body}\n"


def write_file(file_path: Path, memory: Memory) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(render(memory), encoding="utf-8")
