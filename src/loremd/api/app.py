"""Application ASGI : santé, information, et le serveur MCP sous /mcp."""

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
    # Le sous-chemin est porté par le mount : sans cela, la route deviendrait /mcp/mcp.
    mcp.settings.streamable_http_path = "/"

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await service.start()
        logger.info("LoreMd %s prêt — MCP sur %s", __version__, MCP_MOUNT_PATH)
        # Le gestionnaire de sessions MCP a son propre cycle de vie : le monter
        # ne suffit pas, il faut le faire tourner.
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                await service.stop()

    app = FastAPI(
        title="LoreMd",
        version=__version__,
        description="Mémoire partagée pour agents IA, en Markdown, auto-hébergée.",
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


def run() -> None:  # pragma: no cover - point d'entrée uvicorn
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
