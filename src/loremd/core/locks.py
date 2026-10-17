"""Verrou d'écriture par espace.

Une seule écriture à la fois par dépôt : git ne supporte pas deux commits
concurrents dans le même arbre de travail. Le verrou combine un `asyncio.Lock`
(concurrence interne au processus) et un `flock` sur un fichier de l'espace
(concurrence entre le serveur, la CLI et le watcher).
"""

from __future__ import annotations

import asyncio
import fcntl
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

_locks: dict[str, asyncio.Lock] = {}

LOCK_DIRNAME = ".loremd"
LOCK_FILENAME = "write.lock"


def _in_process_lock(slug: str) -> asyncio.Lock:
    lock = _locks.get(slug)
    if lock is None:
        lock = asyncio.Lock()
        _locks[slug] = lock
    return lock


def _acquire_file_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        os.close(fd)
        raise
    return fd


def _release_file_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@asynccontextmanager
async def space_lock(slug: str, space_path: Path) -> AsyncIterator[None]:
    """Prend le verrou d'écriture de l'espace pour la durée du bloc."""
    lock_path = Path(space_path) / LOCK_DIRNAME / LOCK_FILENAME
    async with _in_process_lock(slug):
        fd = await asyncio.to_thread(_acquire_file_lock, lock_path)
        try:
            yield
        finally:
            await asyncio.to_thread(_release_file_lock, fd)
