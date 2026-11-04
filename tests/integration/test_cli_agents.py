"""`loremd agent create` against a real database.

The command's operation is captured instead of run through `run_with_core`,
then awaited on the test's own core.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from loremd.cli import main as cli
from loremd.core.service import Loremd
from loremd.db.repositories import AgentRepository
from loremd.errors import NotFound


class _Captured(Exception):
    pass


def _capture_operation(
    monkeypatch: pytest.MonkeyPatch, **options: Any
) -> Callable[[Loremd], Awaitable[Any]]:
    captured: list[Callable[[Loremd], Awaitable[Any]]] = []

    def fake_run_with_core(operation: Callable[[Loremd], Awaitable[Any]]) -> Any:
        captured.append(operation)
        raise _Captured

    monkeypatch.setattr(cli, "run_with_core", fake_run_with_core)
    with pytest.raises(_Captured):
        cli.agent_create(**options)
    return captured[0]


async def test_unknown_space_does_not_create_the_agent(
    core: Loremd, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = _capture_operation(
        monkeypatch, name="bot", description="new bot", space=["typo"]
    )
    with pytest.raises(NotFound):
        await operation(core)

    async with core.db.acquire() as connection:
        assert await AgentRepository(connection).get_by_name("bot") is None


async def test_unknown_space_leaves_an_existing_agent_untouched(
    core: Loremd, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with core.db.acquire() as connection:
        await AgentRepository(connection).create("bot", "original", review_required=False)

    operation = _capture_operation(
        monkeypatch, name="bot", description="overwritten", space=["typo"], review=True
    )
    with pytest.raises(NotFound):
        await operation(core)

    async with core.db.acquire() as connection:
        agent = await AgentRepository(connection).get_by_name("bot")
    assert agent is not None
    assert agent["description"] == "original"
    assert agent["review_required"] is False
