"""ASGI application: health, info, and the MCP server under /mcp."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .. import __version__
from ..config import Settings, get_settings
from ..core.service import Loremd
from .context import CallContextMiddleware
from .mcp_server import build_mcp

logger = logging.getLogger(__name__)

MCP_MOUNT_PATH = "/mcp"


def create_app(settings: Settings | None = None, *, core: Loremd | None = None) -> FastAPI:
    settings = settings or get_settings()
    service = core or Loremd(settings)
    mcp = build_mcp(service)
    # The sub-path comes from the mount: otherwise the route would become /mcp/mcp.
    mcp.settings.streamable_http_path = "/"

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await service.start()
        logger.info("LoreMd %s ready — MCP at %s", __version__, MCP_MOUNT_PATH)
        # The MCP session manager has its own lifecycle: mounting it is not
        # enough, it has to be running.
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                await service.stop()

    app = FastAPI(
        title="LoreMd",
        version=__version__,
        description="Shared memory for AI agents, in Markdown, self-hosted.",
        lifespan=lifespan,
    )
    app.state.core = service

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> dict[str, object]:
        async with service.db.acquire() as connection:
            await connection.execute("SELECT 1")
        spaces = await service.spaces.list()
        return {"status": "ok", "spaces": len(spaces)}

    @app.get("/", tags=["ops"])
    async def root() -> dict[str, str]:
        return {
            "name": "loremd",
            "version": __version__,
            "mcp_endpoint": f"{settings.server.public_url.rstrip('/')}{MCP_MOUNT_PATH}",
            "transport": "streamable-http",
        }

    app.mount(MCP_MOUNT_PATH, CallContextMiddleware(mcp.streamable_http_app()))
    return app


def run() -> None:  # pragma: no cover - uvicorn entry point
    import uvicorn

    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_app(settings),
        host=settings.server.host,
        port=settings.server.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
