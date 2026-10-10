"""Reading and writing `.md` files with YAML frontmatter.

Rendering is deterministic: same key order, ISO 8601 dates, body ending with a
newline. Two identical writes therefore produce the same file, and git sees no
noise.
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

#: Order in which frontmatter keys are written.
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
    """Splits the frontmatter from the body. Raises if the header is missing or invalid."""
    stripped = text.lstrip("﻿")
    if not stripped.startswith(DELIMITER):
        raise ValidationError("missing frontmatter: the file must start with ---")

    lines = stripped.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == DELIMITER:
            header = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            break
    else:
        raise ValidationError("unterminated frontmatter: closing --- delimiter missing")

    try:
        data = yaml.safe_load(header) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("the frontmatter must be a YAML mapping")
    return data, body.strip()


def parse(text: str, *, space: str, path: str) -> Memory:
    """Builds a validated memory from a file's content."""
    data, body = split_document(text)
    try:
        frontmatter = Frontmatter(**data)
    except PydanticValidationError as exc:
        raise ValidationError(f"invalid frontmatter in {path}: {exc}") from exc
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
    """Renders the memory in file format."""
    dumped = memory.frontmatter.model_dump()
    ordered: dict[str, Any] = {}
    for key in KEY_ORDER:
        if key in dumped:
            ordered[key] = _serialize(dumped.pop(key))
    for key in sorted(dumped):  # extra fields kept as is
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
