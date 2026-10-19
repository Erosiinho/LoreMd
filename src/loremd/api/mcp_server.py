"""MCP server: a thin layer on top of the core.

Nine tools, no more: agents follow a small set of well-described tools better.
Every call re-authenticates the token and re-filters spaces.
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
    resume_to_dict,
    space_to_dict,
    write_result_to_dict,
)

logger = logging.getLogger(__name__)

#: Types `memory_write` accepts: handoffs go through `handoff_save`.
WRITABLE_TYPES = tuple(t for t in MemoryType if t is not MemoryType.HANDOFF)


class ToolFailure(Exception):
    """Error returned to the agent, worded so it can correct itself."""


def _fail(error: LoremdError) -> ToolFailure:
    return ToolFailure(f"{error.code}: {error}")


def build_mcp(core: Loremd) -> FastMCP:
    """Builds the MCP server wired to an already configured core."""

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
                f"unknown type: {type}. Allowed values: "
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
                "hint": "Nothing relevant in memory for this query.",
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
                f"unknown type: {type}. Allowed values: "
                + ", ".join(t.value for t in WRITABLE_TYPES)
            ) from error
        if memory_type is MemoryType.HANDOFF:
            raise ToolFailure(
                "a handoff is written with handoff_save, which renders its sections"
            )
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

    @mcp.tool(description=TOOL_DESCRIPTIONS["handoff_save"])
    async def handoff_save(
        space: str,
        title: str,
        summary: str,
        decisions: list[str] | str | None = None,
        progress: list[str] | str | None = None,
        open_questions: list[str] | str | None = None,
        next_actions: list[str] | str | None = None,
        project: str | None = None,
        sources: list[str] | None = None,
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        try:
            who = await principal()
            result = await core.handoffs.save(
                who,
                space_slug=space,
                title=title,
                summary=summary,
                decisions=decisions,
                progress=progress,
                open_questions=open_questions,
                next_actions=next_actions,
                project=project,
                sources=sources,
                supersedes=supersedes,
            )
        except LoremdError as error:
            raise _fail(error) from error
        payload = write_result_to_dict(result)
        payload["supersedes"] = result.memory.frontmatter.supersedes
        return payload

    @mcp.tool(description=TOOL_DESCRIPTIONS["context_resume"])
    async def context_resume(
        space: str,
        project: str | None = None,
        query: str | None = None,
        limit: int = 5,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        try:
            who = await principal()
            result = await core.handoffs.resume(
                who,
                space_slug=space,
                project=project,
                query=query,
                limit=limit,
                max_chars=max_chars,
            )
        except LoremdError as error:
            raise _fail(error) from error
        return resume_to_dict(result, space=space, project=project)

    return mcp
