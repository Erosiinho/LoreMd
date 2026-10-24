"""The review queue, per space and per agent, and the `verified` flag."""

from __future__ import annotations

from pathlib import Path

import pytest

from loremd.auth.principal import Principal, Scope
from loremd.core import frontmatter
from loremd.core.models import MemoryStatus, MemoryType
from loremd.core.service import Loremd
from loremd.errors import ConflictError, ForbiddenError

REVIEWER = Principal.admin("alice@acme.com", email="alice@acme.com")


def agent(space_id, *, review: bool = False) -> Principal:
    return Principal(
        name="codex-ci",
        scopes=frozenset({Scope.READ, Scope.WRITE}),
        space_ids=frozenset({space_id}),
        review_required=review,
    )


async def write(core: Loremd, who: Principal, content: str, space: str = "product", **kw):
    return await core.memories.write(
        who, content=content, memory_type=MemoryType.DECISION, space_slug=space, **kw
    )


async def test_an_agent_under_review_writes_pending_in_any_space(core: Loremd) -> None:
    space = await core.spaces.create("product")
    reviewed = agent(space.id, review=True)

    result = await write(core, reviewed, "We deploy on Tuesdays.")

    assert result.pending
    assert result.memory.frontmatter.status is MemoryStatus.PENDING
    assert (await core.search.search(reviewed, query="Tuesdays")).status == "no_match"
    # Another agent in the same space is not affected.
    other = await write(core, agent(space.id), "We bill in euros.")
    assert other.memory.frontmatter.status is MemoryStatus.ACTIVE


async def test_approval_publishes_verified_and_signed_by_the_reviewer(core: Loremd) -> None:
    space = await core.spaces.create("regulated", review_required=True)
    written = await write(core, agent(space.id), "Audit logs are kept ten years.", "regulated")

    assert [m.id for m in await core.review.queue(REVIEWER)] == [written.memory.id]
    approved = await core.review.approve(REVIEWER, written.memory.id)

    assert approved.frontmatter.status is MemoryStatus.ACTIVE
    assert approved.frontmatter.verified
    found = await core.search.search(REVIEWER, query="audit logs")
    assert [hit.memory.id for hit in found.matches] == [written.memory.id]
    assert found.matches[0].memory.verified
    commit = core.spaces.repo_for(space).history(written.memory.path)[0]
    assert commit.message.startswith("review: approve")
    assert (commit.author, commit.email) == ("alice@acme.com", "alice@acme.com")
    assert await core.review.queue(REVIEWER) == []


async def test_rejection_keeps_the_file_out_of_search(core: Loremd) -> None:
    space = await core.spaces.create("regulated", review_required=True)
    written = await write(core, agent(space.id), "We store passwords in plain text.", "regulated")

    await core.review.reject(REVIEWER, written.memory.id, "against policy")

    on_disk = frontmatter.read_file(
        Path(space.path) / written.memory.path,
        space="regulated",
        relative_path=written.memory.path,
    )
    assert on_disk.frontmatter.status is MemoryStatus.REJECTED
    assert (await core.search.search(REVIEWER, query="passwords")).status == "no_match"
    commit = core.spaces.repo_for(space).history(written.memory.path)[0]
    assert commit.message.endswith("— against policy")


async def test_only_pending_memories_can_be_approved(core: Loremd, admin: Principal) -> None:
    await core.spaces.create("product")
    written = await write(core, admin, "We use Postgres.")

    with pytest.raises(ConflictError):
        await core.review.approve(REVIEWER, written.memory.id)


async def test_a_supersede_under_review_keeps_the_old_memory_until_approval(
    core: Loremd, admin: Principal
) -> None:
    space = await core.spaces.create("product")
    old = await write(core, admin, "We use MySQL for the catalog.", title="Catalog database")
    reviewed = agent(space.id, review=True)

    replacement = await core.memories.supersede(
        reviewed, old.memory.id, "We use Postgres for the catalog.", "migration"
    )

    assert replacement.pending
    # Until a human approves, searches still find the current decision.
    found = await core.search.search(admin, query="catalog")
    assert [hit.memory.id for hit in found.matches] == [old.memory.id]

    await core.review.approve(REVIEWER, replacement.memory.id)

    found = await core.search.search(admin, query="catalog")
    assert [hit.memory.id for hit in found.matches] == [replacement.memory.id]
    retired = await core.memories.get(admin, old.memory.id)
    assert retired.status is MemoryStatus.SUPERSEDED


async def test_an_update_under_review_goes_back_to_the_queue_unverified(
    core: Loremd, admin: Principal
) -> None:
    space = await core.spaces.create("regulated", review_required=True)
    written = await write(core, agent(space.id), "Backups run nightly.", "regulated")
    await core.review.approve(REVIEWER, written.memory.id)

    updated = await core.memories.update(
        agent(space.id), written.memory.id, "Backups run hourly.", "more often"
    )

    assert updated.pending
    assert updated.memory.frontmatter.status is MemoryStatus.PENDING
    assert not updated.memory.frontmatter.verified


async def test_an_update_drops_the_verified_flag(core: Loremd, admin: Principal) -> None:
    await core.spaces.create("product")
    written = await write(core, admin, "We use Postgres.")
    await core.review.set_verified(REVIEWER, written.memory.id, True)
    assert (await core.memories.get(admin, written.memory.id)).verified

    await core.memories.update(admin, written.memory.id, "We use Postgres 16.", "pinned")

    assert not (await core.memories.get(admin, written.memory.id)).verified


async def test_verifying_twice_makes_a_single_commit(core: Loremd, admin: Principal) -> None:
    space = await core.spaces.create("product")
    written = await write(core, admin, "We use Postgres.")

    await core.review.set_verified(REVIEWER, written.memory.id, True)
    await core.review.set_verified(REVIEWER, written.memory.id, True)

    messages = [c.message for c in core.spaces.repo_for(space).history(written.memory.path)]
    assert messages.count("review: verify We use Postgres.") == 1


async def test_agents_cannot_review(core: Loremd) -> None:
    space = await core.spaces.create("regulated", review_required=True)
    writer = agent(space.id)
    written = await write(core, writer, "Audit logs are kept ten years.", "regulated")

    with pytest.raises(ForbiddenError):
        await core.review.approve(writer, written.memory.id)
    with pytest.raises(ForbiddenError):
        await core.review.queue(writer)


async def test_counts_report_the_queue_per_space(core: Loremd, admin: Principal) -> None:
    space = await core.spaces.create("regulated", review_required=True)
    await core.spaces.create("product")
    await write(core, agent(space.id), "Audit logs are kept ten years.", "regulated")
    await write(core, admin, "We use Postgres.")

    counts = await core.review.counts(REVIEWER)

    assert counts["regulated"]["pending"] == 1
    assert counts["product"]["active"] == 1


async def test_space_review_toggle_is_committed_to_the_repository(core: Loremd) -> None:
    space = await core.spaces.create("product")

    updated = await core.spaces.set_review_required(
        space, True, author_name="alice@acme.com", author_email="alice@acme.com"
    )

    assert updated.review_required
    assert core.spaces.read_space_config(Path(space.path))["review_required"] is True
    assert core.spaces.repo_for(space).history(".loremd/space.yaml")[0].author == (
        "alice@acme.com"
    )
