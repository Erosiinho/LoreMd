"""The phase 1 pipelines, end to end: write, search, update, supersede, reindex.

These exercise the real SQL, the real git repositories and the real files —
the parts the unit suite cannot reach.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loremd.auth.principal import Principal, Scope
from loremd.core import frontmatter
from loremd.core.models import MemoryStatus, MemoryType
from loremd.core.service import Loremd
from loremd.db.repositories import AuditRepository, MemoryIndexRepository
from loremd.errors import ForbiddenError, NotFound, SecretDetected

DECISION = MemoryType.DECISION


async def write_decision(
    core: Loremd, who: Principal, space: str, content: str, **kwargs
):
    return await core.memories.write(
        who, content=content, memory_type=DECISION, space_slug=space, **kwargs
    )


# --------------------------------------------------------------------- spaces


async def test_space_create_makes_a_git_repository(core: Loremd) -> None:
    space = await core.spaces.create("product", description="Product memory")

    root = Path(space.path)
    assert (root / ".git").is_dir()
    assert (root / "decisions").is_dir()
    assert (root / "README.md").exists()
    assert core.spaces.read_space_config(root)["description"] == "Product memory"
    assert core.spaces.repo_for(space).head() is not None


async def test_space_create_is_idempotent(core: Loremd) -> None:
    first = await core.spaces.create("product")
    second = await core.spaces.create("product")

    assert first.id == second.id
    assert len(await core.spaces.list()) == 1


async def test_spaces_are_independent(core: Loremd, admin: Principal) -> None:
    await core.spaces.create("product")
    await core.spaces.create("support")

    await write_decision(core, admin, "product", "We use Postgres for the catalog.")
    found = await core.search.search(admin, query="Postgres", space_slug="support")

    assert found.status == "no_match"


# ---------------------------------------------------------------------- write


async def test_write_creates_file_commit_and_index(core: Loremd, admin: Principal) -> None:
    space = await core.spaces.create("product")

    result = await write_decision(
        core,
        admin,
        "product",
        "We use Postgres with Drizzle as the ORM.\nReason: end-to-end typing.",
        title="API stack",
        project="shop",
        tags=["backend", "db"],
    )

    assert result.created
    assert result.commit is not None
    assert result.memory.path.startswith("decisions/api-stack-")

    # The file is the truth.
    memory = frontmatter.read_file(
        Path(space.path) / result.memory.path, space="product", relative_path=result.memory.path
    )
    assert memory.frontmatter.id == result.memory.id
    assert memory.frontmatter.project == "shop"
    assert memory.frontmatter.tags == ["backend", "db"]
    assert memory.frontmatter.status is MemoryStatus.ACTIVE

    # The commit carries the agent's name.
    commits = core.spaces.repo_for(space).history(result.memory.path)
    assert commits[0].author == "test-agent"
    assert commits[0].email == "test-agent@agents.test"

    # And the index knows about it.
    indexed = await core.memories.get(admin, result.memory.id)
    assert indexed.title == "API stack"
    assert indexed.space == "product"


async def test_write_derives_a_title_when_none_is_given(
    core: Loremd, admin: Principal
) -> None:
    await core.spaces.create("product")

    result = await write_decision(
        core, admin, "product", "We bill in euros. Reason: our customers are in the EU."
    )

    assert result.memory.frontmatter.title == "We bill in euros."


async def test_write_rejects_a_secret_without_committing(
    core: Loremd, admin: Principal
) -> None:
    space = await core.spaces.create("product")
    before = core.spaces.repo_for(space).head()

    with pytest.raises(SecretDetected):
        await write_decision(
            core, admin, "product", "Deploy key is AKIAIOSFODNN7EXAMPLE, keep it safe."
        )

    assert core.spaces.repo_for(space).head() == before
    assert list((Path(space.path) / "decisions").glob("*.md")) == []


async def test_review_required_space_writes_pending(core: Loremd, admin: Principal) -> None:
    await core.spaces.create("regulated", review_required=True)

    result = await write_decision(
        core, admin, "regulated", "Audit logs are kept for ten years."
    )

    assert result.pending
    assert result.memory.frontmatter.status is MemoryStatus.PENDING
    # Pending memories stay out of search until a human reviews them.
    assert (await core.search.search(admin, query="audit logs")).status == "no_match"


async def test_identical_write_returns_the_existing_memory(
    core: Loremd, admin: Principal
) -> None:
    await core.spaces.create("product")
    first = await write_decision(
        core, admin, "product", "We use Postgres with Drizzle as the ORM.", title="API stack"
    )

    second = await write_decision(
        core, admin, "product", "We use Postgres with Drizzle as the ORM.", title="API stack"
    )

    assert not second.created
    assert second.duplicate_of == first.memory.id
    assert second.similarity is not None and second.similarity >= 0.95
    async with core.db.acquire() as connection:
        assert await MemoryIndexRepository(connection).count(
            (await core.spaces.get("product")).id
        ) == 1


async def test_a_different_decision_is_not_a_duplicate(
    core: Loremd, admin: Principal
) -> None:
    await core.spaces.create("product")
    await write_decision(core, admin, "product", "We use Postgres with Drizzle as the ORM.")

    other = await write_decision(
        core, admin, "product", "Deployments happen on Tuesday mornings only."
    )

    assert other.created


# --------------------------------------------------------------------- search


async def test_search_finds_by_keyword_and_reports_provenance(
    core: Loremd, admin: Principal
) -> None:
    await core.spaces.create("product")
    written = await write_decision(
        core,
        admin,
        "product",
        "We use Postgres with Drizzle as the ORM.",
        title="API stack",
        project="shop",
    )

    found = await core.search.search(admin, query="Drizzle")

    assert found.status == "ok"
    assert [hit.memory.id for hit in found.matches] == [written.memory.id]
    hit = found.matches[0]
    assert hit.memory.author == "test-agent"
    assert hit.memory.path == written.memory.path
    assert hit.rank_text is not None


async def test_search_answers_no_match_rather_than_noise(
    core: Loremd, admin: Principal
) -> None:
    await core.spaces.create("product")
    await write_decision(core, admin, "product", "We use Postgres with Drizzle as the ORM.")

    found = await core.search.search(admin, query="holiday schedule in Lisbon")

    assert found.status == "no_match"
    assert found.matches == []


async def test_search_filters_on_project_and_type(core: Loremd, admin: Principal) -> None:
    await core.spaces.create("product")
    # Both mention Postgres, so only the filters can tell them apart.
    await write_decision(
        core, admin, "product", "The shop API stores orders in Postgres.", project="shop"
    )
    await core.memories.write(
        admin,
        content="The blog stores its articles in Postgres too.",
        memory_type=MemoryType.NOTE,
        space_slug="product",
        project="blog",
    )

    unfiltered = await core.search.search(admin, query="Postgres")
    by_project = await core.search.search(admin, query="Postgres", project="blog")
    by_type = await core.search.search(
        admin, query="Postgres", memory_type=MemoryType.DECISION
    )

    assert len(unfiltered.matches) == 2
    assert [hit.memory.project for hit in by_project.matches] == ["blog"]
    assert [hit.memory.type for hit in by_type.matches] == [MemoryType.DECISION]


async def test_search_respects_the_limit(core: Loremd, admin: Principal) -> None:
    await core.spaces.create("product")
    for index in range(5):
        await write_decision(
            core, admin, "product", f"Service number {index} runs on Postgres."
        )

    found = await core.search.search(admin, query="Postgres", limit=2)

    assert len(found.matches) == 2


# ------------------------------------------------------- update and supersede


async def test_update_rewrites_the_file_and_commits_the_reason(
    core: Loremd, admin: Principal
) -> None:
    space = await core.spaces.create("product")
    written = await write_decision(
        core, admin, "product", "We use Postgres.", title="API stack"
    )

    updated = await core.memories.update(
        admin, written.memory.id, "We use Postgres 16.", "version pinned"
    )

    assert updated.memory.content == "We use Postgres 16."
    on_disk = (Path(space.path) / written.memory.path).read_text(encoding="utf-8")
    assert "We use Postgres 16." in on_disk
    commits = core.spaces.repo_for(space).history(written.memory.path)
    assert commits[0].message == "update: API stack — version pinned"
    assert (await core.memories.get(admin, written.memory.id)).content == "We use Postgres 16."


async def test_update_keeps_the_id_and_the_creation_date(
    core: Loremd, admin: Principal
) -> None:
    await core.spaces.create("product")
    written = await write_decision(core, admin, "product", "We use Postgres.")

    updated = await core.memories.update(
        admin, written.memory.id, "We use Postgres 16.", "version pinned"
    )

    assert updated.memory.id == written.memory.id
    assert updated.memory.frontmatter.created == written.memory.frontmatter.created
    assert updated.memory.frontmatter.updated > written.memory.frontmatter.created


async def test_supersede_retires_the_old_memory(core: Loremd, admin: Principal) -> None:
    space = await core.spaces.create("product")
    old = await write_decision(
        core, admin, "product", "We use MySQL for the catalog.", title="Catalog database"
    )

    replacement = await core.memories.supersede(
        admin, old.memory.id, "We use Postgres for the catalog.", "migrated in October"
    )

    assert replacement.created
    assert replacement.memory.frontmatter.supersedes == old.memory.id

    # The old file stays, marked superseded: history is never deleted.
    retired = frontmatter.read_file(
        Path(space.path) / old.memory.path, space="product", relative_path=old.memory.path
    )
    assert retired.frontmatter.status is MemoryStatus.SUPERSEDED

    # Only the replacement is searchable.
    found = await core.search.search(admin, query="catalog")
    assert [hit.memory.id for hit in found.matches] == [replacement.memory.id]


async def test_history_lists_the_commits_of_a_memory(
    core: Loremd, admin: Principal
) -> None:
    await core.spaces.create("product")
    written = await write_decision(
        core, admin, "product", "We use Postgres.", title="API stack"
    )
    await core.memories.update(admin, written.memory.id, "We use Postgres 16.", "pinned")

    commits = await core.memories.history(admin, written.memory.id)

    assert [commit.message for commit in commits] == [
        "update: API stack — pinned",
        "decision: API stack",
    ]
    assert {commit.author for commit in commits} == {"test-agent"}


async def test_unknown_memory_is_reported_as_missing(
    core: Loremd, admin: Principal
) -> None:
    await core.spaces.create("product")

    with pytest.raises(NotFound):
        await core.memories.get(admin, "01JA8KZ3M9X2QH7R4T6VABCDEF")


# ------------------------------------------------------------------- reindex


async def test_reindex_rebuilds_the_index_from_the_files(
    core: Loremd, admin: Principal
) -> None:
    space = await core.spaces.create("product")
    written = await write_decision(core, admin, "product", "We use Postgres with Drizzle.")

    async with core.db.acquire() as connection:
        await MemoryIndexRepository(connection).clear_space(space.id)
    assert (await core.search.search(admin, query="Drizzle")).status == "no_match"

    report = await core.indexer.reindex_space(await core.spaces.get("product"))

    assert report.indexed == 1
    assert report.errors == []
    found = await core.search.search(admin, query="Drizzle")
    assert [hit.memory.id for hit in found.matches] == [written.memory.id]


async def test_reindex_reports_an_invalid_file_instead_of_failing(
    core: Loremd, admin: Principal
) -> None:
    space = await core.spaces.create("product")
    await write_decision(core, admin, "product", "We use Postgres with Drizzle.")
    (Path(space.path) / "decisions" / "broken.md").write_text(
        "no frontmatter here", encoding="utf-8"
    )

    report = await core.indexer.reindex_space(await core.spaces.get("product"))

    assert report.indexed == 1
    assert report.skipped == 1
    assert "broken.md" in report.errors[0]


async def test_sync_picks_up_a_human_edit(core: Loremd, admin: Principal) -> None:
    """An edit made in Obsidian arrives as a commit, and gets reindexed."""
    space = await core.spaces.create("product")
    written = await write_decision(
        core, admin, "product", "We use Postgres.", title="API stack"
    )

    path = Path(space.path) / written.memory.path
    memory = frontmatter.read_file(
        path, space="product", relative_path=written.memory.path
    )
    memory.content = "We use Postgres, decided by the team in Lisbon."
    frontmatter.write_file(path, memory)
    repo = core.spaces.repo_for(space)
    repo.commit(
        [written.memory.path],
        "docs: clarify",
        author_name="alice",
        author_email="alice@acme.com",
    )

    report = await core.indexer.sync_space(await core.spaces.get("product"))

    assert report.indexed == 1
    found = await core.search.search(admin, query="Lisbon")
    assert [hit.memory.id for hit in found.matches] == [written.memory.id]


async def test_sync_removes_a_deleted_file_from_the_index(
    core: Loremd, admin: Principal
) -> None:
    space = await core.spaces.create("product")
    written = await write_decision(core, admin, "product", "We use Postgres with Drizzle.")

    (Path(space.path) / written.memory.path).unlink()
    core.spaces.repo_for(space).commit(
        [written.memory.path],
        "chore: drop a memory",
        author_name="alice",
        author_email="alice@acme.com",
    )

    report = await core.indexer.sync_space(await core.spaces.get("product"))

    assert report.deleted == 1
    assert (await core.search.search(admin, query="Drizzle")).status == "no_match"


async def test_sync_falls_back_to_a_full_reindex_when_the_base_commit_is_gone(
    core: Loremd, admin: Principal
) -> None:
    space = await core.spaces.create("product")
    await write_decision(core, admin, "product", "We use Postgres with Drizzle.")
    async with core.db.acquire() as connection:
        await MemoryIndexRepository(connection).clear_space(space.id)
    # As if the repository had been rewritten under our feet.
    await core.spaces.set_last_indexed_commit(space, "0" * 40)

    report = await core.indexer.sync_space(await core.spaces.get("product"))

    assert report.indexed == 1


async def test_a_file_replaced_with_a_new_id_reindexes_cleanly(
    core: Loremd, admin: Principal
) -> None:
    """A human can rewrite a note from scratch, id included."""
    space = await core.spaces.create("product")
    written = await write_decision(core, admin, "product", "We use Postgres with Drizzle.")

    path = Path(space.path) / written.memory.path
    memory = frontmatter.read_file(
        path, space="product", relative_path=written.memory.path
    )
    memory.frontmatter.id = "01JA8KZ3M9X2QH7R4T6VZZZZZZ"
    frontmatter.write_file(path, memory)

    report = await core.indexer.reindex_space(await core.spaces.get("product"))

    assert report.indexed == 1
    assert (await core.memories.get(admin, "01JA8KZ3M9X2QH7R4T6VZZZZZZ")).space == "product"


# ------------------------------------------------------------- acl and audit


async def test_a_token_only_reaches_its_own_spaces(core: Loremd, admin: Principal) -> None:
    product = await core.spaces.create("product")
    await core.spaces.create("support")
    await write_decision(core, admin, "support", "Refunds are handled within 14 days.")

    scoped = Principal(
        name="codex-ci",
        scopes=frozenset({Scope.READ, Scope.WRITE}),
        space_ids=frozenset({product.id}),
    )

    assert (await core.spaces.list(scoped)) == [product]
    assert (await core.search.search(scoped, query="refunds")).status == "no_match"
    with pytest.raises(ForbiddenError):
        await write_decision(core, scoped, "support", "We also refund shipping.")


async def test_a_read_only_token_cannot_write(core: Loremd) -> None:
    space = await core.spaces.create("product")
    reader = Principal(
        name="reader", scopes=frozenset({Scope.READ}), space_ids=frozenset({space.id})
    )

    with pytest.raises(ForbiddenError, match="write"):
        await write_decision(core, reader, "product", "We use Postgres.")


async def test_a_write_token_can_update_and_supersede(core: Loremd) -> None:
    space = await core.spaces.create("product")
    writer = Principal(
        name="writer", scopes=frozenset({Scope.WRITE}), space_ids=frozenset({space.id})
    )
    written = await write_decision(core, writer, "product", "We use Postgres.")

    updated = await core.memories.update(
        writer, written.memory.id, "We use Postgres 16.", "pinned"
    )
    replaced = await core.memories.supersede(
        writer, updated.memory.id, "We use CockroachDB.", "migrated"
    )

    assert replaced.memory.frontmatter.supersedes == written.memory.id


async def test_every_call_leaves_an_audit_entry(core: Loremd, admin: Principal) -> None:
    await core.spaces.create("product")
    written = await write_decision(core, admin, "product", "We use Postgres with Drizzle.")
    await core.search.search(admin, query="Drizzle")
    await core.memories.get(admin, written.memory.id)

    async with core.db.acquire() as connection:
        entries = await AuditRepository(connection).tail(10)

    actions = [entry["action"] for entry in entries]
    assert {"memory_write", "memory_search", "memory_get"} <= set(actions)
    assert {entry["actor"] for entry in entries} == {"test-agent"}


async def test_the_audit_log_cannot_be_rewritten(core: Loremd, admin: Principal) -> None:
    await core.spaces.create("product")
    await write_decision(core, admin, "product", "We use Postgres with Drizzle.")

    async with core.db.acquire() as connection:
        await connection.execute("DELETE FROM audit_log")
        await connection.execute("UPDATE audit_log SET actor = 'someone-else'")
        remaining = await connection.fetch("SELECT actor FROM audit_log")

    assert remaining
    assert {row["actor"] for row in remaining} == {"test-agent"}
