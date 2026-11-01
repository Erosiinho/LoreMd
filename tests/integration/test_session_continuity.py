"""Handoffs and `resume`, at the core: the supersede chain, review, reindex."""

from __future__ import annotations

from pathlib import Path

import pytest

from loremd.auth.principal import Principal, Scope
from loremd.core import frontmatter
from loremd.core.models import MemoryStatus, MemoryType
from loremd.core.service import Loremd
from loremd.errors import ConflictError, SecretDetected, ValidationError

REVIEWER = Principal.admin("alice@acme.com", email="alice@acme.com")


def agent(space_id, name: str = "claude-code-macbook", *, review: bool = False) -> Principal:
    return Principal(
        name=name,
        scopes=frozenset({Scope.READ, Scope.WRITE}),
        space_ids=frozenset({space_id}),
        review_required=review,
    )


async def save(core: Loremd, who: Principal, title: str = "Shop API — auth refactor", **kw):
    fields = {
        "space_slug": "product",
        "title": title,
        "summary": "Moving the shop API from sessions to JWT.",
        "project": "shop",
    }
    fields.update(kw)
    return await core.handoffs.save(who, **fields)


async def test_a_handoff_is_a_readable_file_with_its_sections(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)
    decision = await core.memories.write(
        who,
        content="Access tokens expire after 15 minutes.",
        memory_type=MemoryType.DECISION,
        space_slug="product",
        project="shop",
    )

    result = await save(
        core,
        who,
        decisions=["Access tokens expire after 15 minutes."],
        next_actions=["Finish the refresh endpoint"],
        sources=[decision.memory.id],
    )

    assert result.created and result.commit
    assert result.memory.path.startswith("handoffs/shop-api-auth-refactor-")
    on_disk = (Path(space.path) / result.memory.path).read_text(encoding="utf-8")
    assert "type: handoff" in on_disk
    assert "## Summary\n\nMoving the shop API from sessions to JWT." in on_disk
    assert "## Next actions\n\n- [ ] Finish the refresh endpoint" in on_disk
    parsed = frontmatter.parse(on_disk, space="product", path=result.memory.path)
    assert parsed.frontmatter.sources == [decision.memory.id]
    assert parsed.frontmatter.author == "claude-code-macbook"
    assert parsed.frontmatter.supersedes is None


async def test_a_new_handoff_supersedes_the_previous_one(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)

    first = await save(core, who)
    second = await save(core, who, title="Shop API — refresh tokens", summary="Refresh next.")
    third = await save(core, who, title="Shop API — cleanup", summary="Cleanup left.")

    assert second.memory.frontmatter.supersedes == first.memory.id
    assert third.memory.frontmatter.supersedes == second.memory.id
    assert third.memory.frontmatter.title == "Shop API — cleanup"
    assert (await core.memories.get(who, first.memory.id)).status is MemoryStatus.SUPERSEDED
    assert (await core.memories.get(who, second.memory.id)).status is MemoryStatus.SUPERSEDED
    resumed = await core.handoffs.resume(who, space_slug="product", project="shop")
    assert [entry.memory.id for entry in resumed.handoffs] == [third.memory.id]


async def test_each_project_has_its_own_thread(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)

    shop = await save(core, who)
    billing = await save(core, who, title="Billing — invoices", project="billing")
    loose = await save(core, who, title="Space cleanup", project=None)

    assert billing.memory.frontmatter.supersedes is None
    assert loose.memory.frontmatter.supersedes is None
    everything = await core.handoffs.resume(who, space_slug="product")
    assert {entry.memory.id for entry in everything.handoffs} == {
        shop.memory.id,
        billing.memory.id,
        loose.memory.id,
    }
    only_billing = await core.handoffs.resume(who, space_slug="product", project="billing")
    assert [entry.memory.id for entry in only_billing.handoffs] == [billing.memory.id]


async def test_an_explicit_supersedes_must_be_the_head_of_a_thread(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)
    first = await save(core, who)
    await save(core, who)
    decision = await core.memories.write(
        who, content="We use JWT.", memory_type=MemoryType.DECISION, space_slug="product"
    )

    with pytest.raises(ConflictError):
        await save(core, who, supersedes=first.memory.id)
    with pytest.raises(ValidationError):
        await save(core, who, supersedes=decision.memory.id)
    with pytest.raises(ValidationError):
        await save(core, who, supersedes="01JANOTAREALID0000000000000")


async def test_an_explicit_supersedes_inherits_the_project(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)
    first = await save(core, who)

    second = await save(core, who, project=None, supersedes=first.memory.id)

    assert second.memory.frontmatter.project == "shop"


async def test_invented_sources_are_refused(core: Loremd) -> None:
    space = await core.spaces.create("product")

    with pytest.raises(ValidationError, match="unknown memory ids"):
        await save(core, agent(space.id), sources=["01JANOTAREALID0000000000000"])


async def test_a_secret_in_any_section_is_refused(core: Loremd) -> None:
    space = await core.spaces.create("product")

    with pytest.raises(SecretDetected):
        await save(core, agent(space.id), progress=["Deployed with AKIAIOSFODNN7EXAMPLE"])
    assert not list((Path(space.path) / "handoffs").glob("*.md"))


async def test_two_identical_handoffs_are_not_deduplicated(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)

    first = await save(core, who)
    second = await save(core, who)

    assert second.created and second.memory.id != first.memory.id


async def test_search_leaves_handoffs_out_unless_asked(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)
    handoff = await save(core, who, summary="Kerberos migration halfway.")

    assert (await core.search.search(who, query="Kerberos")).status == "no_match"
    found = await core.search.search(who, query="Kerberos", memory_type=MemoryType.HANDOFF)
    assert [hit.memory.id for hit in found.matches] == [handoff.memory.id]


async def test_resume_brings_the_relevant_decisions_and_rules(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)
    decision = await core.memories.write(
        who,
        content="Access tokens are JWT signed with EdDSA.",
        memory_type=MemoryType.DECISION,
        space_slug="product",
        project="shop",
    )
    rule = await core.memories.write(
        who,
        content="Never log a JWT, even truncated.",
        memory_type=MemoryType.RULE,
        space_slug="product",
    )
    await core.memories.write(
        who,
        content="Alice prefers JWT examples in Python.",
        memory_type=MemoryType.PREFERENCE,
        space_slug="product",
    )
    await save(core, who, summary="JWT migration of the shop API.")

    resumed = await core.handoffs.resume(who, space_slug="product", project="shop")

    assert resumed.status == "ok"
    assert resumed.query == "Shop API — auth refactor\nJWT migration of the shop API."
    ids = {entry.memory.id for entry in resumed.memories}
    # The project's decision, and a rule that belongs to no project.
    assert {decision.memory.id, rule.memory.id} <= ids
    assert {entry.memory.type for entry in resumed.memories} <= {
        MemoryType.DECISION,
        MemoryType.RULE,
    }


async def test_resume_without_handoff_still_answers_a_query(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)
    await core.memories.write(
        who, content="We deploy with Kamal.", memory_type=MemoryType.DECISION, space_slug="product"
    )

    resumed = await core.handoffs.resume(who, space_slug="product", query="Kamal")

    assert resumed.status == "ok"
    assert resumed.handoffs == []
    assert len(resumed.memories) == 1


async def test_resume_on_an_empty_space_is_no_match(core: Loremd) -> None:
    space = await core.spaces.create("product")

    resumed = await core.handoffs.resume(agent(space.id), space_slug="product")

    assert resumed.status == "no_match"


async def test_review_keeps_the_previous_handoff_until_approval(core: Loremd) -> None:
    space = await core.spaces.create("product")
    first = await save(core, agent(space.id))
    reviewed = agent(space.id, "codex-ci", review=True)

    second = await save(core, reviewed, title="Shop API — refresh tokens")

    assert second.pending
    assert second.memory.frontmatter.supersedes == first.memory.id
    resumed = await core.handoffs.resume(reviewed, space_slug="product", project="shop")
    assert [entry.memory.id for entry in resumed.handoffs] == [first.memory.id]

    await core.review.approve(REVIEWER, second.memory.id)

    resumed = await core.handoffs.resume(reviewed, space_slug="product", project="shop")
    assert [entry.memory.id for entry in resumed.handoffs] == [second.memory.id]
    assert resumed.handoffs[0].memory.verified
    assert (await core.memories.get(REVIEWER, first.memory.id)).status is MemoryStatus.SUPERSEDED


async def test_reindex_restores_identical_resume_results(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)
    decision = await core.memories.write(
        who,
        content="Access tokens are JWT signed with EdDSA.",
        memory_type=MemoryType.DECISION,
        space_slug="product",
        project="shop",
    )
    await save(core, who)
    await save(
        core,
        who,
        title="Shop API — refresh tokens",
        summary="JWT refresh tokens are next.",
        sources=[decision.memory.id],
    )
    await save(core, who, title="Billing — invoices", project="billing")

    def snapshot(result):
        return [
            (entry.memory.id, entry.memory.status, entry.memory.supersedes,
             entry.memory.sources, entry.content)
            for entry in result.handoffs + result.memories
        ]

    before = snapshot(await core.handoffs.resume(who, space_slug="product"))
    before_shop = snapshot(await core.handoffs.resume(who, space_slug="product", project="shop"))

    async with core.db.acquire() as connection:
        await connection.execute("DELETE FROM memory_index")
    report = await core.indexer.reindex_space(await core.spaces.get("product"))

    assert report.errors == []
    assert snapshot(await core.handoffs.resume(who, space_slug="product")) == before
    after_shop = snapshot(await core.handoffs.resume(who, space_slug="product", project="shop"))
    assert after_shop == before_shop
    assert after_shop[0][3] == [decision.memory.id]


async def test_resume_needs_read_scope(core: Loremd) -> None:
    space = await core.spaces.create("product")
    blind = Principal(
        name="writer-only", scopes=frozenset({Scope.WRITE}), space_ids=frozenset({space.id})
    )

    with pytest.raises(Exception, match="read"):
        await core.handoffs.resume(blind, space_slug="product")


async def test_resume_is_audited(core: Loremd) -> None:
    space = await core.spaces.create("product")
    who = agent(space.id)
    handoff = await save(core, who)

    await core.handoffs.resume(who, space_slug="product", project="shop")

    async with core.db.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT target, meta FROM audit_log WHERE action = 'context_resume'"
        )
    assert row["target"] == "shop"
    assert handoff.memory.id in row["meta"]
