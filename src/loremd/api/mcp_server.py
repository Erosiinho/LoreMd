"""Serveur MCP : une fine couche au-dessus du core.

Sept outils, pas plus : des agents suivent mieux un petit jeu d'outils bien
décrits. Chaque appel ré-authentifie le token et refiltre les espaces.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..auth.principal import Principal
from ..core.models import MemoryType
from ..core.service import Loremd
from ..errors import LoremdError
from ..instructions import SERVER_INSTRUCTIONS, TOOL_DESCRIPTIONS
from .context import current_context
from .serializers import (
    commit_to_dict,
    hit_to_dict,
    memory_to_dict,
    space_to_dict,
    write_result_to_dict,
)

logger = logging.getLogger(__name__)


class ToolFailure(Exception):
    """Erreur renvoyée à l'agent, formulée pour qu'il puisse se corriger."""


def _fail(error: LoremdError) -> ToolFailure:
    return ToolFailure(f"{error.code}: {error}")


def build_mcp(core: Loremd) -> FastMCP:
    """Construit le serveur MCP branché sur un core déjà configuré."""

    mcp = FastMCP(
        name="loremd",
        instructions=SERVER_INSTRUCTIONS,
        stateless_http=True,
        json_response=False,
    )

    async def principal() -> Principal:
        call = current_context()
        return await core.auth.authenticate(call.token, ip=call.ip)

    @mcp.tool(description=TOOL_DESCRIPTIONS["spaces_list"])
    async def spaces_list() -> dict[str, Any]:
        try:
            who = await principal()
            spaces = await core.spaces.list(who)
        except LoremdError as error:
            raise _fail(error) from error
        return {"spaces": [space_to_dict(space) for space in spaces]}

    @mcp.tool(description=TOOL_DESCRIPTIONS["memory_search"])
    async def memory_search(
        query: str,
        space: str | None = None,
        project: str | None = None,
        type: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        try:
            memory_type = MemoryType(type) if type else None
        except ValueError as error:
            raise ToolFailure(
                f"type inconnu : {type}. Valeurs possibles : "
                + ", ".join(t.value for t in MemoryType)
            ) from error
        try:
            who = await principal()
            response = await core.search.search(
                who,
                query=query,
                space_slug=space,
                project=project,
                memory_type=memory_type,
                limit=limit,
            )
        except LoremdError as error:
            raise _fail(error) from error

        if response.status == "no_match":
            return {
                "matches": [],
                "status": "no_match",
                "hint": "Rien de pertinent en mémoire pour cette requête.",
            }
        return {
            "matches": [hit_to_dict(hit) for hit in response.matches],
            "status": "ok",
        }

    @mcp.tool(description=TOOL_DESCRIPTIONS["memory_get"])
    async def memory_get(id: str) -> dict[str, Any]:
        try:
            who = await principal()
            memory = await core.memories.get(who, id)
        except LoremdError as error:
            raise _fail(error) from error
        return memory_to_dict(memory)

    @mcp.tool(description=TOOL_DESCRIPTIONS["memory_write"])
    async def memory_write(
        content: str,
        type: str,
        space: str,
        title: str | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        try:
            memory_type = MemoryType(type)
        except ValueError as error:
            raise ToolFailure(
                f"type inconnu : {type}. Valeurs possibles : "
                + ", ".join(t.value for t in MemoryType)
            ) from error
        try:
            who = await principal()
            result = await core.memories.write(
                who,
                content=content,
                memory_type=memory_type,
                space_slug=space,
                title=title,
                project=project,
                tags=tags,
                source=source,
            )
        except LoremdError as error:
            raise _fail(error) from error
        return write_result_to_dict(result)

    @mcp.tool(description=TOOL_DESCRIPTIONS["memory_update"])
    async def memory_update(id: str, content: str, reason: str) -> dict[str, Any]:
        try:
            who = await principal()
            result = await core.memories.update(who, id, content, reason)
        except LoremdError as error:
            raise _fail(error) from error
        return write_result_to_dict(result)

    @mcp.tool(description=TOOL_DESCRIPTIONS["memory_supersede"])
    async def memory_supersede(id: str, new_content: str, reason: str) -> dict[str, Any]:
        try:
            who = await principal()
            result = await core.memories.supersede(who, id, new_content, reason)
        except LoremdError as error:
            raise _fail(error) from error
        return write_result_to_dict(result)

    @mcp.tool(description=TOOL_DESCRIPTIONS["memory_history"])
    async def memory_history(id: str) -> dict[str, Any]:
        try:
            who = await principal()
            commits = await core.memories.history(who, id)
        except LoremdError as error:
            raise _fail(error) from error
        return {"id": id, "commits": [commit_to_dict(commit) for commit in commits]}

    return mcp
