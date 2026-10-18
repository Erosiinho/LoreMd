"""How a path both sides changed is settled during a sync."""

from __future__ import annotations

from loremd.core import frontmatter
from loremd.core.models import Memory, MemoryStatus
from loremd.core.sync import CONFLICT_COPY, CONFLICT_OF, conflict_copy_path, merge_conflicted


def _version(memory: Memory, content: str) -> str:
    edited = memory.model_copy(deep=True)
    edited.content = content
    return frontmatter.render(edited)


def test_copy_path_sits_next_to_the_original() -> None:
    assert (
        conflict_copy_path("decisions/api-stack-abcdef.md", "01JA8KZ3M9X2QH7R4T6VXYZ123")
        == "decisions/api-stack-abcdef-conflict-xyz123.md"
    )


def test_both_versions_are_kept_and_flagged(memory: Memory) -> None:
    ours = _version(memory, "Agent says: Postgres 16.")
    theirs = _version(memory, "Human says: Postgres 15.")

    merged = merge_conflicted(memory.path, ours, theirs, space="product")

    assert len(merged.writes) == 2
    remote = frontmatter.parse(merged.writes[memory.path] or "", space="product", path=memory.path)
    copy_path = next(path for path in merged.writes if path != memory.path)
    copy = frontmatter.parse(merged.writes[copy_path] or "", space="product", path=copy_path)

    # The remote (human) version keeps the path and the id.
    assert remote.id == memory.id
    assert remote.content == "Human says: Postgres 15."
    assert remote.frontmatter.status is MemoryStatus.CONFLICT
    # The server (agent) version is a copy with its own id, pointing back.
    assert copy.id != memory.id
    assert copy.content == "Agent says: Postgres 16."
    assert copy.frontmatter.status is MemoryStatus.CONFLICT
    assert copy.frontmatter.extra(CONFLICT_OF) == memory.id
    assert remote.frontmatter.extra(CONFLICT_COPY) == copy.id
    assert set(merged.conflicts) == {memory.id, copy.id}


def test_a_deleted_side_keeps_the_other_flagged(memory: Memory) -> None:
    ours = _version(memory, "Agent kept editing this.")

    merged = merge_conflicted(memory.path, ours, None, space="product")

    kept = frontmatter.parse(merged.writes[memory.path] or "", space="product", path=memory.path)
    assert kept.content == "Agent kept editing this."
    assert kept.frontmatter.status is MemoryStatus.CONFLICT
    assert merged.conflicts == [memory.id]
    assert merged.note is None


def test_a_broken_side_keeps_the_readable_one(memory: Memory) -> None:
    ours = _version(memory, "Agent version.")

    merged = merge_conflicted(memory.path, ours, "no frontmatter at all", space="product")

    kept = frontmatter.parse(merged.writes[memory.path] or "", space="product", path=memory.path)
    assert kept.content == "Agent version."
    assert merged.note is not None and "invalid frontmatter" in merged.note


def test_a_file_that_is_not_a_memory_takes_the_remote_version() -> None:
    merged = merge_conflicted("README.md", "server readme", "human readme", space="product")

    assert merged.writes == {"README.md": "human readme"}
    assert merged.conflicts == []
