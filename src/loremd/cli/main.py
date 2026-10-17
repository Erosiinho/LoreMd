"""LoreMd administration commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from .. import __version__
from ..auth.principal import Principal
from ..auth.tokens import generate_token
from ..core.models import MemoryType
from ..core.service import Loremd
from ..db.repositories import AgentRepository, AuditRepository, SpaceRepository, TokenRepository
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
app.add_typer(space_app)
app.add_typer(agent_app)
app.add_typer(token_app)


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


# --------------------------------------------------------------------------- spaces


@space_app.command("create")
def space_create(
    slug: str,
    remote: Annotated[str | None, typer.Option(help="URL of the space's git remote.")] = None,
    description: Annotated[str | None, typer.Option(help="Short description.")] = None,
    review: Annotated[
        bool, typer.Option("--review/--no-review", help="Require human review.")
    ] = False,
) -> None:
    """Creates a space: git repository, directory tree and index entry."""

    async def operation(core: Loremd):
        return await core.spaces.create(
            slug, description=description, remote_url=remote, review_required=review
        )

    space = run_with_core(operation)
    console.print(f"[green]space created[/green] {space.slug} → {space.path}")
    if remote:
        console.print(f"remote: {remote}")


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
    pull: Annotated[bool, typer.Option("--pull/--no-pull", help="git pull --rebase first.")] = True,
    push: Annotated[bool, typer.Option("--push/--no-push", help="git push afterwards.")] = False,
) -> None:
    """Fetches commits from the remote (human edits) and reindexes the diff."""

    async def operation(core: Loremd):
        spaces = [await core.spaces.get(slug)] if slug else await core.spaces.list()
        reports = []
        for space in spaces:
            repo = core.spaces.repo_for(space)
            if pull and repo.has_remote():
                repo.pull_rebase()
            reports.append(await core.indexer.sync_space(space))
            if push and repo.has_remote():
                repo.push()
        return reports

    for report in run_with_core(operation):
        console.print(
            f"{report.space}: {report.indexed} indexed, {report.deleted} deleted"
            + (f", {report.skipped} skipped" if report.skipped else "")
        )
        for error in report.errors:
            console.print(f"  [yellow]{error}[/yellow]")


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
) -> None:
    """Creates an agent and, if spaces are given, its first token."""
    scopes = [item.strip() for item in scope.split(",") if item.strip()]

    async def operation(core: Loremd):
        async with core.db.acquire() as connection:
            agent = await AgentRepository(connection).create(name, description)
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

    table = Table("agent", "description", "created", "state")
    for row in run_with_core(operation):
        table.add_row(
            row["name"],
            row["description"] or "—",
            row["created_at"].date().isoformat(),
            "disabled" if row["disabled_at"] else "active",
        )
    console.print(table)


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
    scopes = [item.strip() for item in scope.split(",") if item.strip()]
    if not scopes:
        raise typer.BadParameter("at least one scope is required")

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
    """Checks the setup: database, pgvector, dimensions, spaces, git."""
    from ..core.git import GitRepo

    async def operation(core: Loremd):
        checks: list[tuple[str, bool, str]] = []
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
