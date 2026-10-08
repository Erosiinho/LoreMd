from __future__ import annotations

from loremd.ids import is_ulid, memory_filename, new_ulid, slugify


def test_ulid_has_the_right_shape() -> None:
    value = new_ulid()

    assert len(value) == 26
    assert is_ulid(value)


def test_ulids_sort_by_date() -> None:
    older = new_ulid(1_700_000_000_000)
    newer = new_ulid(1_800_000_000_000)

    assert older < newer


def test_slugify_transliterates_and_cleans() -> None:
    assert slugify("API stack — Postgres + Drizzle") == "api-stack-postgres-drizzle"
    assert slugify("Préférence : indentation à 4 espaces") == "preference-indentation-a-4-espaces"
    assert slugify("///") == "memory"


def test_slugify_cuts_on_a_whole_word() -> None:
    slug = slugify("a really very long title that goes beyond the imposed limit", max_length=20)

    assert len(slug) <= 20
    assert not slug.endswith("-")


def test_filename_combines_slug_and_id_suffix() -> None:
    name = memory_filename("API stack", "01JA8KZ3M9X2QH7R4T6VABCDEF")

    assert name == "api-stack-abcdef.md"
