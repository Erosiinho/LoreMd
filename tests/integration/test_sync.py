"""Two-way sync, end to end: agents write on the server, humans push to the remote.

The remote is a bare repository on disk and the human a clone of it, which is
exactly what an Obsidian user has.
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from loremd.auth.principal import Principal
from loremd.config import Settings
from loremd.core import frontmatter
from loremd.core.git import GitRepo
from loremd.core.models import Frontmatter, Memory, MemoryStatus, MemoryType, Space
from loremd.core.service import Loremd
from loremd.core.sync import CONFLICT_OF
from loremd.db.repositories import AuditRepository
from loremd.embeddings.hash_provider import HashEmbeddingProvider
from loremd.errors import ConflictError
from loremd.ids import new_ulid

DECISION = MemoryType.DECISION


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(path)], check=True, capture_output=True
    )
    return path


class Human:
    """Alice, editing the space in her own clone."""

    def __init__(self, remote: Path, where: Path) -> None:
        subprocess.run(
            ["git", "clone", str(remote), str(where)], check=True, capture_output=True
        )
        self.repo = GitRepo(where)

    def pull(self) -> None:
        self.repo.run("pull", "--rebase", "origin", "main")

    def read(self, relative: str) -> Memory:
        return frontmatter.read_file(
            self.repo.path / relative, space="product", relative_path=relative
        )

    def save(self, memory: Memory, message: str) -> None:
        frontmatter.write_file(self.repo.path / memory.path, memory)
        self.commit([memory.path], message)

    def commit(self, paths: list[str], message: str) -> None:
        self.repo.commit(paths, message, author_name="alice", author_email="alice@acme.com")
        self.repo.push()


async def synced_space(core: Loremd, remote: Path) -> Space:
    space = await core.spaces.create("product", remote_url=str(remote))
    report = await core.sync.sync_space(space)
    assert report.error is None and report.pushed
    return await core.spaces.get("product")


async def write(core: Loremd, who: Principal, content: str, **kwargs) -> Memory:
    result = await core.memories.write(
        who, content=content, memory_type=DECISION, space_slug="product", **kwargs
    )
    return result.memory


# -------------------------------------------------------------- both directions


async def test_an_agent_write_reaches_the_remote(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> None:
    space = await synced_space(core, remote)
    written = await write(core, admin, "We use Postgres with Drizzle.", title="API stack")

    report = await core.sync.sync_space(space)

    assert report.pushed
    alice = Human(remote, tmp_path / "alice")
    assert alice.read(written.path).content == "We use Postgres with Drizzle."
    assert alice.repo.history(written.path)[0].author == "test-agent"


async def test_a_human_push_is_pulled_and_searchable(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> None:
    space = await synced_space(core, remote)
    written = await write(core, admin, "We use Postgres.", title="API stack")
    await core.sync.sync_space(space)

    alice = Human(remote, tmp_path / "alice")
    memory = alice.read(written.path)
    memory.content = "We use Postgres, decided by the team in Lisbon."
    alice.save(memory, "docs: clarify")

    report = await core.sync.sync_space(space)

    assert report.pulled == 1
    assert report.conflicts == []
    found = await core.search.search(admin, query="Lisbon")
    assert [hit.memory.id for hit in found.matches] == [written.id]
    history = await core.memories.history(admin, written.id)
    assert history[0].author == "alice"


async def test_independent_changes_are_rebased_without_a_merge_commit(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> None:
    space = await synced_space(core, remote)
    alice = Human(remote, tmp_path / "alice")
    (alice.repo.path / "notes").mkdir(exist_ok=True)
    human_note = await _human_memory(alice, "notes/team-lunch-abcdef.md", "Lunch is at noon.")
    agent = await write(core, admin, "Deployments happen on Tuesdays.")

    report = await core.sync.sync_space(space)

    assert report.pulled == 1 and report.pushed and report.conflicts == []
    repo = core.spaces.repo_for(space)
    merges = repo.run("rev-list", "--merges", "--count", "HEAD")
    assert merges == "0"
    alice.pull()
    assert (alice.repo.path / agent.path).exists()
    assert (await core.search.search(admin, query="Lunch")).matches[0].memory.id == human_note


async def test_a_cloned_space_is_indexed(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> None:
    """A company that already has a memory repository brings it as is."""
    seed = GitRepo(tmp_path / "seed")
    seed.init()
    seed.set_remote(str(remote))
    (seed.path / ".loremd").mkdir()
    (seed.path / ".loremd" / "space.yaml").write_text(
        "description: Existing memory\nreview_required: true\n", encoding="utf-8"
    )
    seed.commit([".loremd/space.yaml"], "init", author_name="alice", author_email="a@acme.com")
    seed.push()
    alice = Human(remote, tmp_path / "alice")
    memory_id = await _human_memory(alice, "facts/vat-rate-abcdef.md", "VAT is 20 percent.")

    space = await core.spaces.create("product", remote_url=str(remote))
    report = await core.indexer.sync_space(space)

    assert report.indexed == 1
    # The repository's own config wins over unset options.
    assert space.review_required
    assert space.description == "Existing memory"
    assert (await core.memories.get(admin, memory_id)).content == "VAT is 20 percent."


async def test_a_human_change_to_the_space_config_reaches_the_index(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> None:
    space = await synced_space(core, remote)
    assert not space.review_required
    alice = Human(remote, tmp_path / "alice")
    config = alice.repo.path / ".loremd" / "space.yaml"
    config.write_text("description: null\nreview_required: true\n", encoding="utf-8")
    alice.commit([".loremd/space.yaml"], "chore: require review")

    await core.sync.sync_space(space)

    assert (await core.spaces.get("product")).review_required
    assert (await write(core, admin, "Anything new goes to review.")).frontmatter.status is (
        MemoryStatus.PENDING
    )


# ------------------------------------------------------------------ conflicts


async def _conflict(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> tuple[Space, Memory, Human]:
    """The same memory changed on both sides between two syncs."""
    space = await synced_space(core, remote)
    written = await write(core, admin, "We use Postgres.", title="Database")
    await core.sync.sync_space(space)

    alice = Human(remote, tmp_path / "alice")
    memory = alice.read(written.path)
    memory.content = "We use Postgres 15, says the DBA."
    alice.save(memory, "docs: the DBA's version")
    await core.memories.update(admin, written.id, "We use Postgres 16.", "upgrade planned")
    return space, written, alice


async def test_a_conflict_keeps_both_versions_flagged(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> None:
    space, written, alice = await _conflict(core, admin, remote, tmp_path)

    report = await core.sync.sync_space(space)

    assert report.error is None
    assert len(report.conflicts) == 2
    root = Path(space.path)
    at_path = frontmatter.read_file(
        root / written.path, space="product", relative_path=written.path
    )
    # The human version keeps the path; the agent's is kept alongside.
    assert at_path.content == "We use Postgres 15, says the DBA."
    assert at_path.frontmatter.status is MemoryStatus.CONFLICT
    copies = [p for p in (root / "decisions").glob("*-conflict-*.md")]
    assert len(copies) == 1
    copy = frontmatter.read_file(copies[0], space="product", relative_path="x")
    assert copy.content == "We use Postgres 16."
    assert copy.frontmatter.extra(CONFLICT_OF) == written.id

    # Neither version shows up in search, and agents cannot pick one.
    assert (await core.search.search(admin, query="Postgres")).status == "no_match"
    with pytest.raises(ConflictError):
        await core.memories.update(admin, written.id, "We use Postgres 17.", "again")

    # The merge reached the remote: humans see the conflict too.
    assert report.pushed
    alice.pull()
    assert len(list((alice.repo.path / "decisions").glob("*-conflict-*.md"))) == 1


async def test_resolving_a_conflict_keeps_one_verified_memory(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> None:
    space, written, _ = await _conflict(core, admin, remote, tmp_path)
    await core.sync.sync_space(space)
    reviewer = Principal.admin("alice@acme.com", email="alice@acme.com")

    [view] = await core.review.conflicts(reviewer)
    assert view.copy is not None
    kept = await core.review.resolve(reviewer, view.copy.id, "copy")

    assert kept is not None and kept.id == written.id
    root = Path(space.path)
    assert list((root / "decisions").glob("*-conflict-*.md")) == []
    on_disk = frontmatter.read_file(
        root / written.path, space="product", relative_path=written.path
    )
    assert on_disk.content == "We use Postgres 16."
    assert on_disk.frontmatter.status is MemoryStatus.ACTIVE
    assert on_disk.frontmatter.verified
    assert on_disk.frontmatter.extra("conflict_copy") is None
    found = await core.search.search(admin, query="Postgres")
    assert [hit.memory.id for hit in found.matches] == [written.id]
    commit = core.spaces.repo_for(space).history(written.path)[0]
    assert commit.author == "alice@acme.com"
    assert commit.email == "alice@acme.com"
    assert await core.review.conflicts(reviewer) == []


async def test_a_conflict_can_be_resolved_with_merged_text(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> None:
    space, written, _ = await _conflict(core, admin, remote, tmp_path)
    await core.sync.sync_space(space)

    kept = await core.review.resolve(
        admin, written.id, "memory", content="We use Postgres 15 now, 16 after the upgrade."
    )

    assert kept is not None
    assert (await core.memories.get(admin, written.id)).content == (
        "We use Postgres 15 now, 16 after the upgrade."
    )


async def test_a_deleted_memory_edited_by_an_agent_stays_for_review(
    core: Loremd, admin: Principal, remote: Path, tmp_path: Path
) -> None:
    space = await synced_space(core, remote)
    written = await write(core, admin, "We use Postgres.", title="Database")
    await core.sync.sync_space(space)
    alice = Human(remote, tmp_path / "alice")
    (alice.repo.path / written.path).unlink()
    alice.commit([written.path], "chore: drop it")
    await core.memories.update(admin, written.id, "We use Postgres 16.", "upgrade")

    report = await core.sync.sync_space(space)

    assert report.conflicts == [written.id]
    [view] = await core.review.conflicts(admin)
    assert view.copy is None
    assert view.memory.content == "We use Postgres 16."
    assert await core.review.resolve(admin, written.id, "discard") is None
    assert not (Path(space.path) / written.path).exists()


async def test_a_sync_failure_is_recorded_not_raised(core: Loremd, tmp_path: Path) -> None:
    space = await core.spaces.create("product")
    core.spaces.repo_for(space).set_remote(str(tmp_path / "gone.git"))

    report = await core.sync.sync_space(space)

    assert report.error is not None
    assert (await core.spaces.get("product")).last_sync_error == report.error
    async with core.db.acquire() as connection:
        entries = await AuditRepository(connection).tail(5, action="space_sync")
    assert entries and entries[0]["actor"] == "loremd"


# ----------------------------------------------------------- in the background


async def test_auto_push_sends_commits_in_the_background(
    settings: Settings, remote: Path, tmp_path: Path, admin: Principal, core: Loremd
) -> None:
    pushing = Loremd(
        settings.model_copy(
            update={"git": settings.git.model_copy(update={"auto_push": True})}
        ),
        embeddings=HashEmbeddingProvider(settings.embeddings.dimensions),
    )
    await pushing.start()
    try:
        await synced_space(pushing, remote)
        written = await write(pushing, admin, "We use Postgres with Drizzle.")
        assert await pushing.sync.wait(timeout=30)
    finally:
        await pushing.stop()

    alice = Human(remote, tmp_path / "alice")
    assert (alice.repo.path / written.path).exists()


async def test_the_watcher_picks_up_commits_made_outside_loremd(
    settings: Settings, admin: Principal, core: Loremd
) -> None:
    space = await core.spaces.create("product")
    written = await write(core, admin, "We use Postgres.", title="Database")
    path = Path(space.path) / written.path
    memory = frontmatter.read_file(path, space="product", relative_path=written.path)
    memory.content = "We use Postgres, edited on the server by hand."
    frontmatter.write_file(path, memory)
    core.spaces.repo_for(space).commit(
        [written.path], "hand edit", author_name="ops", author_email="ops@acme.com"
    )

    core.settings.sync.interval_seconds = 1
    try:
        core.sync.start_watcher()
        for _ in range(100):
            await asyncio.sleep(0.05)
            await core.sync.wait(timeout=10)
            if (await core.search.search(admin, query="hand")).status == "ok":
                break
    finally:
        core.settings.sync.interval_seconds = 0

    found = await core.search.search(admin, query="hand")
    assert [hit.memory.id for hit in found.matches] == [written.id]


async def _human_memory(human: Human, relative: str, content: str) -> str:
    """Alice writes a memory by hand, as the Obsidian guide describes."""
    now = datetime.now(UTC)
    memory_id = new_ulid()
    memory = Memory(
        frontmatter=Frontmatter(
            id=memory_id,
            type=MemoryType.FACT if relative.startswith("facts/") else MemoryType.NOTE,
            title=content.rstrip("."),
            author="alice",
            created=now,
            updated=now,
        ),
        content=content,
        space="product",
        path=relative,
    )
    human.save(memory, f"add {relative}")
    return memory_id
