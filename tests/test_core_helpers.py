from __future__ import annotations

import pytest

from loremd.core.indexer import is_memory_path
from loremd.core.memories import author_email, derive_title
from loremd.core.models import MemoryType
from loremd.core.spaces import validate_slug
from loremd.errors import ValidationError


def test_title_is_derived_from_first_sentence() -> None:
    title = derive_title("We use Postgres. Reason: end-to-end typing.")

    assert title == "We use Postgres."


def test_title_ignores_markdown_markup() -> None:
    assert derive_title("# API stack\n\nDetails") == "API stack"


def test_empty_content_has_no_title() -> None:
    with pytest.raises(ValidationError):
        derive_title("   \n\n  ")


def test_author_email_is_always_valid() -> None:
    email = author_email("Claude Code (MacBook)", "agents.local")

    assert email == "claude-code-macbook@agents.local"
    assert author_email("", "agents.local") == "agent@agents.local"


def test_each_type_has_its_directory() -> None:
    assert MemoryType.DECISION.directory == "decisions"
    assert {t.directory for t in MemoryType} == {
        "decisions",
        "preferences",
        "rules",
        "facts",
        "notes",
        "handoffs",
    }


@pytest.mark.parametrize(
    "path,expected",
    [
        ("decisions/api-stack-abcdef.md", True),
        ("notes/subfolder/note.md", True),
        ("handoffs/shop-auth-refactor-abcdef.md", True),
        ("README.md", False),
        (".loremd/space.yaml", False),
        ("decisions/draft.txt", False),
    ],
)
def test_only_memory_files_are_indexed(path: str, expected: bool) -> None:
    assert is_memory_path(path) is expected


def test_space_slugs_are_constrained() -> None:
    assert validate_slug("internal-product") == "internal-product"
    for invalid in ("Product", "product/sub", "-product", "pro duct", ""):
        with pytest.raises(ValidationError):
            validate_slug(invalid)
