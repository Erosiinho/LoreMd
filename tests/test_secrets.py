from __future__ import annotations

import pytest

from loremd.core import secrets


@pytest.mark.parametrize(
    "content",
    [
        "The key is AKIAIOSFODNN7EXAMPLE, do not commit it.",
        "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "token: ghp_0123456789abcdefghijklmnopqrstuvwxyzAB",
        "connection: postgres://user:sup3rS3cretPwd@db.acme.com:5432/app",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_detects_obvious_secrets(content: str) -> None:
    assert secrets.scan(content)


@pytest.mark.parametrize(
    "content",
    [
        "We use Postgres with Drizzle as the ORM.",
        "api_key = <your-key>",
        "password: change-me",
        "The OPENAI_API_KEY variable is read from the environment.",
        "The dev DSN is postgres://loremd@localhost:5432/loremd",
    ],
)
def test_does_not_block_ordinary_text(content: str) -> None:
    assert secrets.scan(content) == []


def test_secret_is_never_echoed_in_clear() -> None:
    findings = secrets.scan("AKIAIOSFODNN7EXAMPLE")

    assert "AKIAIOSFODNN7EXAMPLE" not in str(findings[0])
