"""Plomberie partagée par les commandes : cycle de vie du core et affichage."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import typer
from rich.console import Console

from ..config import Settings, load_settings
from ..core.service import Loremd
from ..errors import LoremdError

console = Console()
error_console = Console(stderr=True, style="bold red")

T = TypeVar("T")

#: Renseigné par le callback principal de l'application.
CONFIG_PATH: Path | None = None


def settings() -> Settings:
    return load_settings(CONFIG_PATH)


def run_with_core(
    operation: Callable[[Loremd], Awaitable[T]], *, migrate: bool = True
) -> T:
    """Démarre le core, exécute l'opération, et traduit les erreurs du domaine."""

    async def main() -> T:
        core = Loremd(settings())
        await core.start(migrate=migrate)
        try:
            return await operation(core)
        finally:
            await core.stop()

    try:
        return asyncio.run(main())
    except LoremdError as error:
        error_console.print(f"{error.code} : {error}")
        raise typer.Exit(code=1) from error
