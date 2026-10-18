from __future__ import annotations

import pytest
import typer

from loremd.cli.main import parse_scopes


def test_scopes_are_parsed_and_deduplicated() -> None:
    assert parse_scopes("read, write ,read") == ["read", "write"]


def test_scopes_are_case_insensitive() -> None:
    assert parse_scopes("READ,Write") == ["read", "write"]


def test_an_unknown_scope_is_refused() -> None:
    # Stored as is, it would produce a token that silently cannot do anything.
    with pytest.raises(typer.BadParameter, match="unknown scope"):
        parse_scopes("read,wirte")


def test_at_least_one_scope_is_required() -> None:
    with pytest.raises(typer.BadParameter):
        parse_scopes(" , ")
