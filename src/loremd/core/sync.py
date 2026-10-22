"""Two-way sync between a space and its git remote.

Humans edit in Obsidian and push to the remote; agents write on the server.
A sync brings both together: fetch, replay the local commits on top of the
remote (rebase), reindex the diff, push.

When the rebase fails, nothing is thrown away: the remote is merged instead,
and every memory both sides changed is kept twice — the remote version at its
path, the local one in a sibling copy — both with `status: conflict`, until
someone picks in the console.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ..auth.principal import Principal
from ..config import Settings
from ..db import Database
from ..db.repositories import SpaceRepository
from ..errors import GitError, LoremdError, NotFound, ValidationError
from ..ids import new_ulid
from . import frontmatter
from .audit import AuditService
from .git import GitRepo
from .indexer import Indexer, IndexReport, is_memory_path
from .locks import space_lock
from .models import Memory, MemoryStatus, Space
from .spaces import SpaceService

logger = logging.getLogger(__name__)

#: On the remote version: id of the local version kept alongside it.
CONFLICT_COPY = "conflict_copy"
#: On the local version: id of the remote version it conflicts with.
CONFLICT_OF = "conflict_of"

SYNC_AUTHOR = ("loremd", "loremd@localhost")

#: A push rejected because the remote moved since the fetch: fetch again.
_REJECTED_MARKERS = ("rejected", "non-fast-forward", "fetch first")
_PUSH_ATTEMPTS = 3


@dataclass
class SyncReport:
    """What a sync did, for the CLI, the console and the audit log."""

    space: str
    remote: bool = False
    pulled: int = 0
    pushed: bool = False
    conflicts: list[str] = field(default_factory=list)
    index: IndexReport | None = None
    error: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.pulled or self.conflicts or self.error)


@dataclass
class MergedFile:
    """How a conflicted path is settled: what to write (None = delete), what to flag."""

    writes: dict[str, str | None]
    conflicts: list[str] = field(default_factory=list)
    note: str | None = None


def conflict_copy_path(relative_path: str, copy_id: str) -> str:
    """`decisions/api-stack-abcdef.md` → `decisions/api-stack-abcdef-conflict-xyz123.md`."""
    path = PurePosixPath(relative_path)
    return str(path.with_name(f"{path.stem}-conflict-{copy_id[-6:].lower()}{path.suffix}"))


def _parse(text: str | None, space: str, path: str) -> Memory | None:
    if text is None:
        return None
    try:
        return frontmatter.parse(text, space=space, path=path)
    except ValidationError:
        return None


def merge_conflicted(
    path: str,
    ours: str | None,
    theirs: str | None,
    *,
    space: str,
    now: datetime | None = None,
) -> MergedFile:
    """Settles one conflicted path. `ours` is the server's version, `theirs` the remote's.

    The remote version keeps the path: it is what the humans see in their
    clone. A file that is not a memory (README, space config) simply takes the
    remote version — there is nothing to review in it.
    """
    now = now or datetime.now(UTC)
    if not is_memory_path(path):
        return MergedFile(writes={path: theirs if theirs is not None else ours})

    local = _parse(ours, space, path)
    remote = _parse(theirs, space, path)

    if local is not None and remote is not None:
        copy_id = new_ulid()
        copy_path = conflict_copy_path(path, copy_id)
        remote.frontmatter.status = MemoryStatus.CONFLICT
        remote.frontmatter.updated = now
        remote.frontmatter.set_extra(CONFLICT_COPY, copy_id)

        copy = local.model_copy(deep=True)
        copy.path = copy_path
        copy.frontmatter.id = copy_id
        copy.frontmatter.status = MemoryStatus.CONFLICT
        copy.frontmatter.updated = now
        copy.frontmatter.drop_extra(CONFLICT_COPY)
        copy.frontmatter.set_extra(CONFLICT_OF, remote.id)
        return MergedFile(
            writes={path: frontmatter.render(remote), copy_path: frontmatter.render(copy)},
            conflicts=[remote.id, copy_id],
        )

    survivor = remote or local
    if survivor is not None:
        # One side deleted the file, or broke its frontmatter: keep the readable
        # version, flagged, so a human decides. The other one stays in git.
        survivor.frontmatter.status = MemoryStatus.CONFLICT
        survivor.frontmatter.updated = now
        unreadable = (ours if survivor is remote else theirs) is not None
        return MergedFile(
            writes={path: frontmatter.render(survivor)},
            conflicts=[survivor.id],
            note=f"{path}: one version has an invalid frontmatter" if unreadable else None,
        )

    # Neither side parses: keep the remote text and let reindexing report it.
    return MergedFile(
        writes={path: theirs if theirs is not None else ours},
        note=f"{path}: conflict between two versions with an invalid frontmatter",
    )


def _is_rejection(error: GitError) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _REJECTED_MARKERS)


class SyncService:
    """Syncs spaces with their remotes, now or in the background."""

    def __init__(
        self,
        database: Database,
        spaces: SpaceService,
        indexer: Indexer,
        audit: AuditService,
        settings: Settings,
    ) -> None:
        self._db = database
        self._spaces = spaces
        self._indexer = indexer
        self._audit = audit
        self._settings = settings
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._rerun: set[str] = set()
        self._watcher: asyncio.Task[None] | None = None

    # -- one sync ------------------------------------------------------------

    async def sync_space(
        self, space: Space, *, pull: bool = True, push: bool = True
    ) -> SyncReport:
        """Fetch, integrate, reindex, push. Errors land in the report, not in an exception."""
        repo = self._spaces.repo_for(space)
        report = SyncReport(space=space.slug, remote=repo.has_remote())
        timeout = self._settings.git.timeout_seconds
        try:
            for attempt in range(_PUSH_ATTEMPTS):
                remote_branch = False
                notes: list[str] = []
                if report.remote and (pull or attempt > 0):
                    remote_branch = await asyncio.to_thread(repo.fetch, timeout=timeout)
                async with space_lock(space.slug, Path(space.path)):
                    if remote_branch:
                        pulled, conflicts, notes = await asyncio.to_thread(
                            self._integrate, repo, space.slug
                        )
                        report.pulled += pulled
                        report.conflicts.extend(conflicts)
                    current = await self._spaces.refresh_config(
                        await self._spaces.get(space.slug)
                    )
                    report.index = await self._indexer.sync_space(current)
                    report.index.errors.extend(notes)
                if not (report.remote and push):
                    break
                try:
                    report.pushed = await asyncio.to_thread(self._push, repo, remote_branch)
                    break
                except GitError as error:
                    if not pull or attempt == _PUSH_ATTEMPTS - 1 or not _is_rejection(error):
                        raise
                    logger.info("push to %s rejected, fetching again", space.slug)
        except LoremdError as error:
            report.error = str(error)
            logger.warning("sync of %s failed: %s", space.slug, error)

        if report.remote:
            async with self._db.acquire() as connection:
                await SpaceRepository(connection).record_sync(space.id, report.error)
        if report.changed:
            await self._audit.record(
                Principal.system(),
                "space_sync",
                target=space.slug,
                space_id=space.id,
                meta={
                    "pulled": report.pulled,
                    "pushed": report.pushed,
                    "conflicts": report.conflicts,
                    "error": report.error,
                },
            )
        return report

    def _integrate(self, repo: GitRepo, slug: str) -> tuple[int, list[str], list[str]]:
        """Brings the remote commits in. Returns (commits pulled, conflict ids, notes)."""
        ref = repo.remote_ref()
        ahead, behind = repo.ahead_behind(ref)
        if behind == 0:
            return 0, [], []
        if ahead == 0:
            repo.fast_forward(ref)
            return behind, [], []

        identity = {
            "GIT_AUTHOR_NAME": SYNC_AUTHOR[0],
            "GIT_AUTHOR_EMAIL": SYNC_AUTHOR[1],
            "GIT_COMMITTER_NAME": SYNC_AUTHOR[0],
            "GIT_COMMITTER_EMAIL": SYNC_AUTHOR[1],
        }
        if repo.rebase(ref, env=identity):
            return behind, [], []

        # The rebase stopped on a conflict and was aborted. A merge keeps both
        # histories intact and lets every conflicted path be settled at once.
        if repo.merge_no_commit(ref, env=identity):
            repo.commit_staged(
                "sync: merge remote changes",
                author_name=SYNC_AUTHOR[0],
                author_email=SYNC_AUTHOR[1],
            )
            return behind, [], []

        conflicts: list[str] = []
        notes: list[str] = []
        try:
            now = datetime.now(UTC)
            for path in repo.unmerged_paths():
                merged = merge_conflicted(
                    path,
                    repo.stage_content(path, 2),
                    repo.stage_content(path, 3),
                    space=slug,
                    now=now,
                )
                for target, content in merged.writes.items():
                    file_path = repo.path / target
                    if content is None:
                        file_path.unlink(missing_ok=True)
                    else:
                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        file_path.write_text(content, encoding="utf-8")
                repo.stage(list(merged.writes))
                conflicts.extend(merged.conflicts)
                if merged.note:
                    notes.append(merged.note)
            repo.commit_staged(
                f"sync: merge remote changes, {len(conflicts)} memory version(s) in conflict",
                author_name=SYNC_AUTHOR[0],
                author_email=SYNC_AUTHOR[1],
            )
        except Exception:
            repo.abort_merge()
            raise
        return behind, conflicts, notes

    def _push(self, repo: GitRepo, remote_branch: bool) -> bool:
        """Pushes when there is something the remote does not have.

        `remote_branch` says the remote-tracking branch was just fetched, so
        it can be trusted to tell whether anything is missing over there.
        """
        if repo.head() is None:
            return False
        if remote_branch:
            ahead, _ = repo.ahead_behind(repo.remote_ref())
            if ahead == 0:
                return False
        repo.push(timeout=self._settings.git.timeout_seconds)
        return True

    # -- in the background ---------------------------------------------------

    def request(self, slug: str, *, delay: float = 0.0) -> None:
        """Schedules a sync. Requests arriving while one runs are folded into a single rerun."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no event loop: nothing can run in the background
            return
        task = self._tasks.get(slug)
        if task is not None and not task.done():
            self._rerun.add(slug)
            return
        self._tasks[slug] = loop.create_task(self._run(slug, delay), name=f"sync:{slug}")

    def request_push(self, space: Space) -> None:
        """After a commit: push soon, batching the commits that follow closely."""
        if not self._settings.git.auto_push:
            return
        if not self._spaces.repo_for(space).has_remote():
            return
        self.request(space.slug, delay=self._settings.sync.push_debounce_seconds)

    async def _run(self, slug: str, delay: float) -> None:
        while True:
            if delay:
                await asyncio.sleep(delay)
            self._rerun.discard(slug)
            try:
                await self.sync_space(await self._spaces.get(slug))
            except NotFound:
                return
            except Exception:  # noqa: BLE001 - a background task must not die silently
                logger.exception("background sync of %s failed", slug)
            if slug not in self._rerun:
                return

    def start_watcher(self) -> None:
        """Periodic pull and reindex of every space; the webhook only makes it faster."""
        interval = self._settings.sync.interval_seconds
        if interval <= 0 or self._watcher is not None:
            return
        self._watcher = asyncio.get_running_loop().create_task(
            self._watch(interval), name="sync:watcher"
        )

    async def _watch(self, interval: float) -> None:
        while True:
            try:
                for space in await self._spaces.list():
                    self.request(space.slug)
            except Exception:  # noqa: BLE001
                logger.exception("the sync watcher could not list spaces")
            await asyncio.sleep(interval)

    async def wait(self, timeout: float | None = None) -> bool:
        """Waits for the background syncs in flight, reruns included. False on timeout."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while pending := [task for task in self._tasks.values() if not task.done()]:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                return False
            await asyncio.wait(pending, timeout=remaining)
        return True

    async def stop(self) -> None:
        """Stops the watcher and lets pending pushes finish, within the git timeout."""
        if self._watcher is not None:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
            self._watcher = None
        timeout = self._settings.sync.push_debounce_seconds + self._settings.git.timeout_seconds
        if not await self.wait(timeout):
            for task in self._tasks.values():
                task.cancel()
        self._tasks.clear()
