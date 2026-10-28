"""LoreMd administration commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, cast

import typer
from rich.panel import Panel
from rich.table import Table

from .. import __version__
from ..auth import totp
from ..auth.admins import Role
from ..auth.principal import Principal, Scope
from ..auth.tokens import generate_token
from ..core.memories import commit_identity
from ..core.models import MemoryType
from ..core.review import Keep
from ..core.service import Loremd
from ..core.sync import SyncReport
from ..db.repositories import (
    AdminRepository,
    AgentRepository,
    AuditRepository,
    SpaceRepository,
    TokenRepository,
)
from ..errors import NotFound
from . import runtime
from .runtime import console, run_with_core

app = typer.Typer(
    name="loremd",
    help="Shared memory for AI agents, in Markdown, self-hosted.",
    no_args_is_help=True,
)
space_app = typer.Typer(name="space", help="Manage spaces (one space = one git repository).")
agent_app = typer.Typer(name="agent", help="Manage agents.")
token_app = typer.Typer(name="token", help="Manage access tokens.")
review_app = typer.Typer(name="review", help="Work the review queue.")
conflict_app = typer.Typer(name="conflict", help="Resolve sync conflicts.")
admin_app = typer.Typer(name="admin", help="Manage admin console accounts.")
app.add_typer(space_app)
app.add_typer(agent_app)
app.add_typer(token_app)
app.add_typer(review_app)
app.add_typer(conflict_app)
app.add_typer(admin_app)

#: Who signs the commits of review commands run from the CLI.
AuthorOption = Annotated[
    str, typer.Option("--author", help="Commit author, ideally your email.")
]


@app.callback()
def main(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to the config.yaml file."),
    ] = None,
) -> None:
    runtime.CONFIG_PATH = config


@app.command()
def version() -> None:
    """Shows the version."""
    console.print(f"loremd {__version__}")


def parse_scopes(raw: str) -> list[str]:
    """Validates a `read,write` string.

    A typo caught here rather than stored: an unknown scope in the database
    would silently produce a token that can do nothing.
    """
    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not requested:
        raise typer.BadParameter("at least one scope is required")
    allowed = {scope.value for scope in Scope}
    unknown = [scope for scope in requested if scope not in allowed]
    if unknown:
        raise typer.BadParameter(
            f"unknown scope: {', '.join(unknown)} (allowed: {', '.join(sorted(allowed))})"
        )
    return list(dict.fromkeys(requested))


# --------------------------------------------------------------------------- spaces


@space_app.command("create")
def space_create(
    slug: str,
    remote: Annotated[str | None, typer.Option(help="URL of the space's git remote.")] = None,
    description: Annotated[str | None, typer.Option(help="Short description.")] = None,
    review: Annotated[
        bool | None,
        typer.Option(
            "--review/--no-review",
            help="Require human review. Defaults to what the cloned space says, else no.",
        ),
    ] = None,
) -> None:
    """Creates a space: git repository, directory tree and index entry.

    With --remote, an existing repository is cloned and indexed, and the
    initial commit is pushed.
    """

    async def operation(core: Loremd):
        space = await core.spaces.create(
            slug, description=description, remote_url=remote, review_required=review
        )
        indexed = await core.indexer.sync_space(space)
        report = await core.sync.sync_space(space) if remote else None
        return await core.spaces.get(slug), indexed, report

    space, indexed, report = run_with_core(operation)
    console.print(f"[green]space created[/green] {space.slug} → {space.path}")
    if indexed.indexed:
        console.print(f"{indexed.indexed} existing memories indexed")
    if remote:
        console.print(f"remote: {remote}")
    if report is not None:
        _print_sync(report)


@space_app.command("list")
def space_list() -> None:
    """Lists spaces."""

    async def operation(core: Loremd):
        spaces = await core.spaces.list()
        async with core.db.acquire() as connection:
            from ..db.repositories import MemoryIndexRepository

            repository = MemoryIndexRepository(connection)
            counts = {space.slug: await repository.count(space.id) for space in spaces}
        return spaces, counts

    spaces, counts = run_with_core(operation)
    table = Table("space", "memories", "review", "remote", "last indexed commit")
    for space in spaces:
        table.add_row(
            space.slug,
            str(counts[space.slug]),
            "required" if space.review_required else "—",
            space.remote_url or "—",
            (space.last_indexed_commit or "—")[:10],
        )
    console.print(table)


@space_app.command("remote")
def space_remote(slug: str, url: str) -> None:
    """Sets or replaces a space's git remote."""

    async def operation(core: Loremd):
        space = await core.spaces.get(slug)
        core.spaces.repo_for(space).set_remote(url)
        async with core.db.acquire() as connection:
            await SpaceRepository(connection).set_remote(space.id, url)
        return space

    space = run_with_core(operation)
    console.print(f"[green]remote set[/green] for {space.slug}: {url}")


@space_app.command("sync")
def space_sync(
    slug: Annotated[
        str | None, typer.Argument(help="Space to sync; all of them by default.")
    ] = None,
    pull: Annotated[
        bool, typer.Option("--pull/--no-pull", help="Fetch and integrate remote commits.")
    ] = True,
    push: Annotated[bool, typer.Option("--push/--no-push", help="Push local commits.")] = True,
) -> None:
    """Syncs with the remote: human edits come in, agent commits go out, the diff is reindexed."""

    async def operation(core: Loremd):
        spaces = [await core.spaces.get(slug)] if slug else await core.spaces.list()
        return [await core.sync.sync_space(space, pull=pull, push=push) for space in spaces]

    reports = run_with_core(operation)
    for report in reports:
        _print_sync(report)
    if any(report.error for report in reports):
        raise typer.Exit(code=1)


def _print_sync(report: SyncReport) -> None:
    index = report.index
    parts = []
    if report.remote:
        parts.append(f"{report.pulled} commit(s) pulled")
        parts.append("pushed" if report.pushed else "nothing to push")
    if index is not None:
        parts.append(f"{index.indexed} indexed, {index.deleted} deleted")
        if index.skipped:
            parts.append(f"{index.skipped} skipped")
    console.print(f"{report.space}: " + ", ".join(parts))
    if report.conflicts:
        console.print(
            f"  [yellow]{len(report.conflicts)} memory version(s) in conflict[/yellow] — "
            "`loremd conflict list` or the console"
        )
    for error in index.errors if index else []:
        console.print(f"  [yellow]{error}[/yellow]")
    if report.error:
        console.print(f"  [red]{report.error}[/red]")


@space_app.command("review")
def space_review(
    slug: str,
    required: Annotated[
        bool, typer.Option("--on/--off", help="Send every write to the review queue.")
    ],
    author: AuthorOption = "cli",
) -> None:
    """Turns review on or off for a space (committed to .loremd/space.yaml)."""

    async def operation(core: Loremd):
        space = await core.spaces.get(slug)
        identity = commit_identity(
            Principal.admin(author), core.settings.git.author_email_domain
        )
        updated = await core.spaces.set_review_required(space, required, **identity)
        core.sync.request_push(updated)
        return updated

    space = run_with_core(operation)
    state = "required" if space.review_required else "not required"
    console.print(f"[green]{space.slug}[/green]: review {state}")


# --------------------------------------------------------------------------- agents


@agent_app.command("create")
def agent_create(
    name: str,
    description: Annotated[str | None, typer.Option(help="What this agent is for.")] = None,
    space: Annotated[
        list[str] | None, typer.Option("--space", help="Allowed space (repeatable).")
    ] = None,
    scope: Annotated[
        str, typer.Option("--scope", help="Comma-separated scopes.")
    ] = "read,write",
    expires_days: Annotated[
        int | None, typer.Option("--expires-days", help="Token expiry in days.")
    ] = None,
    review: Annotated[
        bool | None,
        typer.Option("--review/--no-review", help="Send this agent's writes to review."),
    ] = None,
) -> None:
    """Creates an agent and, if spaces are given, its first token."""
    scopes = parse_scopes(scope)

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            agent = await AgentRepository(connection).create(
                name, description, review_required=review
            )
            if not space:
                return agent, None, []
            spaces = []
            for slug in space:
                found = await SpaceRepository(connection).get_by_slug(slug)
                if found is None:
                    raise NotFound(f"unknown space: {slug}")
                spaces.append(found)
            parts, token_hash = generate_token()
            expires_at = (
                datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None
            )
            await TokenRepository(connection).create(
                agent_id=agent["id"],
                prefix=parts.prefix,
                token_hash=token_hash,
                scopes=scopes,
                space_ids=[item.id for item in spaces],
                expires_at=expires_at,
            )
            return agent, parts.raw, [item.slug for item in spaces]

    agent, raw_token, slugs = run_with_core(operation)
    console.print(f"[green]agent created[/green] {agent['name']}")
    if raw_token:
        _print_token(raw_token, agent["name"], scopes, slugs)


@agent_app.command("list")
def agent_list() -> None:
    """Lists agents."""

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            return await AgentRepository(connection).list()

    table = Table("agent", "description", "review", "created", "state")
    for row in run_with_core(operation):
        table.add_row(
            row["name"],
            row["description"] or "—",
            "required" if row["review_required"] else "—",
            row["created_at"].date().isoformat(),
            "disabled" if row["disabled_at"] else "active",
        )
    console.print(table)


@agent_app.command("review")
def agent_review(
    name: str,
    required: Annotated[
        bool, typer.Option("--on/--off", help="Send this agent's writes to review.")
    ],
) -> None:
    """Turns review on or off for everything an agent writes, in every space."""

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            repository = AgentRepository(connection)
            if await repository.get_by_name(name) is None:
                raise NotFound(f"unknown agent: {name}")
            await repository.set_review_required(name, required)

    run_with_core(operation)
    console.print(f"[green]{name}[/green]: review {'required' if required else 'not required'}")


@agent_app.command("disable")
def agent_disable(name: str, enable: Annotated[bool, typer.Option("--enable")] = False) -> None:
    """Disables an agent (all of its tokens stop working)."""

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            repository = AgentRepository(connection)
            if await repository.get_by_name(name) is None:
                raise NotFound(f"unknown agent: {name}")
            await repository.set_disabled(name, not enable)

    run_with_core(operation)
    console.print(f"[green]{name}[/green] {'re-enabled' if enable else 'disabled'}")


# --------------------------------------------------------------------------- tokens


def _print_token(raw: str, agent: str, scopes: list[str], spaces: list[str]) -> None:
    console.print(
        Panel(
            f"[bold]{raw}[/bold]\n\n"
            f"agent: {agent}\nscopes: {', '.join(scopes)}\n"
            f"spaces: {', '.join(spaces) or '—'}",
            title="token — shown only once",
            border_style="yellow",
        )
    )


@token_app.command("create")
def token_create(
    agent: str,
    space: Annotated[list[str], typer.Option("--space", help="Allowed space (repeatable).")],
    scope: Annotated[str, typer.Option("--scope")] = "read,write",
    expires_days: Annotated[int | None, typer.Option("--expires-days")] = None,
) -> None:
    """Creates a token for an existing agent."""
    scopes = parse_scopes(scope)

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            found = await AgentRepository(connection).get_by_name(agent)
            if found is None:
                raise NotFound(f"unknown agent: {agent}")
            space_ids = []
            for slug in space:
                resolved = await SpaceRepository(connection).get_by_slug(slug)
                if resolved is None:
                    raise NotFound(f"unknown space: {slug}")
                space_ids.append(resolved.id)
            parts, token_hash = generate_token()
            expires_at = (
                datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None
            )
            await TokenRepository(connection).create(
                agent_id=found["id"],
                prefix=parts.prefix,
                token_hash=token_hash,
                scopes=scopes,
                space_ids=space_ids,
                expires_at=expires_at,
            )
            return parts.raw

    _print_token(run_with_core(operation), agent, scopes, list(space))


@token_app.command("list")
def token_list(agent: Annotated[str | None, typer.Option("--agent")] = None) -> None:
    """Lists tokens (prefixes only: the secret is not stored)."""

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            return await TokenRepository(connection).list(agent)

    table = Table("prefix", "agent", "scopes", "expires", "last used", "state")
    for row in run_with_core(operation):
        table.add_row(
            row["prefix"],
            row["agent_name"],
            ", ".join(row["scopes"]),
            row["expires_at"].date().isoformat() if row["expires_at"] else "—",
            row["last_used_at"].isoformat(timespec="minutes") if row["last_used_at"] else "—",
            "revoked" if row["revoked_at"] else "active",
        )
    console.print(table)


@token_app.command("revoke")
def token_revoke(prefix: str) -> None:
    """Revokes a token immediately."""

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            return await TokenRepository(connection).revoke(prefix)

    if run_with_core(operation):
        console.print(f"[green]token revoked[/green] {prefix}")
    else:
        console.print(f"[yellow]no active token with prefix[/yellow] {prefix}")


# --------------------------------------------------------------------------- review


@review_app.command("list")
def review_list(space: Annotated[str | None, typer.Option("--space")] = None) -> None:
    """Lists the memories waiting for review, oldest first."""

    async def operation(core: Loremd):
        return await core.review.queue(Principal.admin(), space_slug=space)

    memories = run_with_core(operation)
    if not memories:
        console.print("nothing waiting for review")
        return
    for memory in memories:
        console.print(
            f"[bold]{memory.id}[/bold]  {memory.title}  [dim]{memory.space}/{memory.path} "
            f"· {memory.author or '—'} · {memory.updated.isoformat(timespec='minutes')}[/dim]"
        )
        console.print(f"  {memory.content.strip().splitlines()[0][:160]}\n", markup=False)


@review_app.command("approve")
def review_approve(memory_id: str, author: AuthorOption = "cli") -> None:
    """Publishes a pending memory, marked verified."""

    async def operation(core: Loremd):
        return await core.review.approve(Principal.admin(author), memory_id)

    memory = run_with_core(operation)
    console.print(f"[green]approved[/green] {memory.id} — {memory.frontmatter.title}")


@review_app.command("reject")
def review_reject(
    memory_id: str,
    reason: Annotated[str, typer.Option("--reason", help="Becomes the commit message.")] = "",
    author: AuthorOption = "cli",
) -> None:
    """Turns a pending memory down; the file stays with status rejected."""

    async def operation(core: Loremd):
        return await core.review.reject(Principal.admin(author), memory_id, reason)

    memory = run_with_core(operation)
    console.print(f"[yellow]rejected[/yellow] {memory.id} — {memory.frontmatter.title}")


@review_app.command("verify")
def review_verify(
    memory_id: str,
    undo: Annotated[bool, typer.Option("--undo", help="Remove the verified flag.")] = False,
    author: AuthorOption = "cli",
) -> None:
    """Marks a memory as verified by a human (or no longer)."""

    async def operation(core: Loremd):
        return await core.review.set_verified(Principal.admin(author), memory_id, not undo)

    memory = run_with_core(operation)
    state = "verified" if memory.frontmatter.verified else "not verified"
    console.print(f"[green]{memory.id}[/green] {state}")


@conflict_app.command("list")
def conflict_list(space: Annotated[str | None, typer.Option("--space")] = None) -> None:
    """Lists conflicts left by a sync."""

    async def operation(core: Loremd):
        return await core.review.conflicts(Principal.admin(), space_slug=space)

    views = run_with_core(operation)
    if not views:
        console.print("no conflict")
        return
    for view in views:
        console.print(
            f"[bold]{view.memory.id}[/bold]  {view.memory.frontmatter.title}  "
            f"[dim]{view.space}/{view.memory.path}[/dim]"
        )
        if view.copy is not None:
            console.print(f"  server version: {view.copy.id}  [dim]{view.copy.path}[/dim]")


@conflict_app.command("resolve")
def conflict_resolve(
    memory_id: str,
    keep: Annotated[
        str,
        typer.Option(
            "--keep", help="memory (the remote version), copy (the server version) or discard."
        ),
    ],
    author: AuthorOption = "cli",
) -> None:
    """Ends a conflict by keeping one version, or none."""
    if keep not in ("memory", "copy", "discard"):
        raise typer.BadParameter("--keep must be memory, copy or discard")

    async def operation(core: Loremd):
        return await core.review.resolve(
            Principal.admin(author),
            memory_id,
            cast(Keep, keep),
        )

    kept = run_with_core(operation)
    if kept is None:
        console.print("[yellow]conflict resolved[/yellow]: both versions removed")
    else:
        console.print(f"[green]conflict resolved[/green]: {kept.id} is active again")


# --------------------------------------------------------------------------- admins


def _print_totp(email: str, secret: str) -> None:
    console.print(
        Panel(
            f"secret: [bold]{secret}[/bold]\n\n"
            f"{totp.provisioning_uri(secret, email)}\n\n"
            "Add it to an authenticator app (paste the secret, or turn the URI into "
            "a QR code). The console asks for a code from it at every login.",
            title=f"TOTP for {email} — shown only once",
            border_style="yellow",
        )
    )


def _prompt_password() -> str:
    return str(typer.prompt("password", hide_input=True, confirmation_prompt=True))


@admin_app.command("create")
def admin_create(
    email: str,
    role: Annotated[
        str, typer.Option("--role", help="admin (everything) or reviewer (review only).")
    ] = "admin",
) -> None:
    """Creates a console account: prompts for a password, prints the TOTP secret once."""
    try:
        chosen = Role(role)
    except ValueError as error:
        raise typer.BadParameter("--role must be admin or reviewer") from error
    password = _prompt_password()

    async def operation(core: Loremd):
        return await core.admins.create(email, password, chosen)

    created, secret = run_with_core(operation)
    console.print(f"[green]admin created[/green] {created} ({chosen.value})")
    _print_totp(created, secret)


@admin_app.command("list")
def admin_list() -> None:
    """Lists console accounts."""

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            return await AdminRepository(connection).list()

    table = Table("email", "role", "created", "last login", "state")
    for row in run_with_core(operation):
        table.add_row(
            row["email"],
            row["role"],
            row["created_at"].date().isoformat(),
            row["last_login_at"].isoformat(timespec="minutes") if row["last_login_at"] else "—",
            "disabled" if row["disabled_at"] else "active",
        )
    console.print(table)


@admin_app.command("disable")
def admin_disable(email: str, enable: Annotated[bool, typer.Option("--enable")] = False) -> None:
    """Disables a console account and ends its sessions (or re-enables it)."""

    async def operation(core: Loremd):
        await core.admins.set_disabled(email, not enable)

    run_with_core(operation)
    console.print(f"[green]{email}[/green] {'re-enabled' if enable else 'disabled'}")


@admin_app.command("password")
def admin_password(email: str) -> None:
    """Sets a new password for a console account."""
    password = _prompt_password()

    async def operation(core: Loremd):
        await core.admins.set_password(email, password)

    run_with_core(operation)
    console.print(f"[green]password changed[/green] for {email}")


@admin_app.command("reset-totp")
def admin_reset_totp(email: str) -> None:
    """Issues a new TOTP secret, for a lost phone. The old codes stop working."""

    async def operation(core: Loremd):
        return await core.admins.reset_totp(email)

    _print_totp(email, run_with_core(operation))


# --------------------------------------------------------------------------- index


@app.command()
def reindex(
    slug: Annotated[
        str | None, typer.Argument(help="Space to reindex; all of them by default.")
    ] = None,
) -> None:
    """Rebuilds the Postgres index from the files."""

    async def operation(core: Loremd):
        spaces = [await core.spaces.get(slug)] if slug else await core.spaces.list()
        return [await core.indexer.reindex_space(space, full=True) for space in spaces]

    for report in run_with_core(operation):
        console.print(f"{report.space}: {report.indexed} memories indexed")
        for error in report.errors:
            console.print(f"  [yellow]{error}[/yellow]")


# --------------------------------------------------------------------------- memory


@app.command()
def search(
    query: str,
    space: Annotated[str | None, typer.Option("--space")] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 8,
) -> None:
    """Searches memory with administrator rights."""

    async def operation(core: Loremd):
        return await core.search.search(
            Principal.admin(), query=query, space_slug=space, project=project, limit=limit
        )

    response = run_with_core(operation)
    if response.status == "no_match":
        console.print("[yellow]no_match[/yellow] — nothing relevant in memory")
        return
    for hit in response.matches:
        memory = hit.memory
        console.print(
            f"[bold]{memory.title}[/bold]  [dim]{memory.space}/{memory.path} "
            f"· score {hit.score:.4f} · {memory.type.value}[/dim]"
        )
        console.print(f"  {memory.content.strip().splitlines()[0][:160]}\n")


@app.command()
def write(
    space: Annotated[str, typer.Option("--space")],
    content: Annotated[str, typer.Argument(help="Memory content.")],
    type: Annotated[str, typer.Option("--type")] = "note",
    title: Annotated[str | None, typer.Option("--title")] = None,
    project: Annotated[str | None, typer.Option("--project")] = None,
    author: Annotated[str, typer.Option("--author", help="Commit author.")] = "cli",
) -> None:
    """Writes a memory from the command line."""
    try:
        memory_type = MemoryType(type)
    except ValueError as error:
        raise typer.BadParameter(
            f"unknown type: {type} ({', '.join(t.value for t in MemoryType)})"
        ) from error

    async def operation(core: Loremd):
        return await core.memories.write(
            Principal.admin(author),
            content=content,
            memory_type=memory_type,
            space_slug=space,
            title=title,
            project=project,
            source="cli",
        )

    result = run_with_core(operation)
    if result.created:
        console.print(f"[green]memory created[/green] {result.memory.id} → {result.memory.path}")
    else:
        console.print(
            f"[yellow]duplicate[/yellow]: existing memory {result.duplicate_of} "
            f"(similarity {result.similarity:.3f})"
        )


@app.command()
def audit(
    limit: Annotated[int, typer.Option("--limit")] = 30,
    actor: Annotated[str | None, typer.Option("--actor")] = None,
) -> None:
    """Shows the latest audit log entries."""

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            return await AuditRepository(connection).tail(limit, actor)

    table = Table("date", "actor", "action", "target", "ip")
    for row in run_with_core(operation):
        table.add_row(
            row["created_at"].isoformat(timespec="seconds"),
            row["actor"],
            row["action"],
            (row["target"] or "—")[:40],
            str(row["ip"] or "—"),
        )
    console.print(table)


# --------------------------------------------------------------------------- server


@app.command()
def serve(
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    reload: Annotated[bool, typer.Option("--reload")] = False,
) -> None:
    """Starts the MCP server."""
    import logging

    import uvicorn

    from ..api.app import create_app

    configuration = runtime.settings()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_app(configuration),
        host=host or configuration.server.host,
        port=port or configuration.server.port,
        reload=reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


@app.command()
def doctor() -> None:
    """Checks the setup: git, database, pgvector, dimensions, spaces, tokens, admins."""
    import os
    import shutil

    from ..core.git import GitRepo

    async def operation(core: Loremd):
        checks: list[tuple[str, bool, str]] = []

        git_binary = shutil.which("git")
        checks.append(("git binary", git_binary is not None, git_binary or "git is required"))

        root = core.settings.spaces.root
        checks.append(
            (
                "spaces root writable",
                root.is_dir() and os.access(root, os.W_OK),
                str(root),
            )
        )

        async with core.db.acquire() as connection:
            await connection.execute("SELECT 1")
            checks.append(("Postgres connection", True, core.settings.database.dsn.split("@")[-1]))
            has_vector = await connection.fetchval(
                "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"
            )
            checks.append(("pgvector extension", bool(has_vector), ""))
        dimensions = await core.db.embedding_dimensions()
        checks.append(
            (
                "embedding dimensions",
                dimensions == core.settings.embeddings.dimensions,
                f"schema {dimensions} / config {core.settings.embeddings.dimensions}",
            )
        )
        spaces = await core.spaces.list()
        for space in spaces:
            repo = GitRepo(Path(space.path))
            checks.append((f"git repository \"{space.slug}\"", repo.exists, space.path))
        if not spaces:
            checks.append(("spaces", False, "no space: `loremd space create <slug>`"))

        async with core.db.acquire() as connection:
            usable = await connection.fetchval(
                """
                SELECT count(*) FROM tokens t JOIN agents a ON a.id = t.agent_id
                WHERE t.revoked_at IS NULL AND a.disabled_at IS NULL
                  AND (t.expires_at IS NULL OR t.expires_at > now())
                """
            )
        checks.append(
            (
                "usable agent token",
                bool(usable) or bool(core.settings.bootstrap_token),
                f"{usable} active token(s)"
                if usable
                else "no token: `loremd agent create <name> --space <slug>`",
            )
        )
        if core.settings.console.enabled:
            async with core.db.acquire() as connection:
                admins = await connection.fetchval(
                    "SELECT count(*) FROM admins WHERE disabled_at IS NULL"
                )
            checks.append(
                (
                    "console admin",
                    bool(admins),
                    f"{admins} active admin(s)"
                    if admins
                    else "no admin: `loremd admin create <email>`",
                )
            )
        for space in spaces:
            if space.last_sync_error:
                checks.append((f"sync \"{space.slug}\"", False, space.last_sync_error))
        if core.settings.bootstrap_token:
            checks.append(
                (
                    "bootstrap token unset",
                    False,
                    "LOREMD_BOOTSTRAP_TOKEN grants full access to every space — "
                    "unset it once real tokens exist",
                )
            )
        return checks

    failures = 0
    for label, ok, detail in run_with_core(operation):
        mark = "[green]ok[/green]" if ok else "[red]ko[/red]"
        failures += 0 if ok else 1
        console.print(f"{mark}  {label}" + (f"  [dim]{detail}[/dim]" if detail else ""))
    if failures:
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
