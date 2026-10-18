from __future__ import annotations

import base64

import pytest

from loremd.auth import totp

#: The RFC 6238 test key, base32-encoded.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")],
)
def test_codes_match_the_rfc_vectors(timestamp: int, expected: str) -> None:
    assert totp.code_at(RFC_SECRET, totp.current_step(timestamp)) == expected


def test_a_code_from_the_previous_step_is_accepted() -> None:
    now = 1234567890.0
    previous = totp.code_at(RFC_SECRET, totp.current_step(now) - 1)

    assert totp.match(RFC_SECRET, previous, now=now) == totp.current_step(now) - 1


def test_a_code_outside_the_window_is_refused() -> None:
    now = 1234567890.0
    stale = totp.code_at(RFC_SECRET, totp.current_step(now) - 3)

    assert totp.match(RFC_SECRET, stale, now=now) is None


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef"])
def test_malformed_codes_are_refused(code: str) -> None:
    assert totp.match(RFC_SECRET, code) is None


def test_spaces_in_a_typed_code_are_ignored() -> None:
    now = 59.0
    assert totp.match(RFC_SECRET, "287 082", now=now) is not None


def test_generated_secrets_are_usable_and_distinct() -> None:
    first, second = totp.generate_secret(), totp.generate_secret()

    assert first != second
    assert len(totp.code_at(first, 1)) == totp.DIGITS


def test_provisioning_uri_names_the_issuer_and_account() -> None:
    uri = totp.provisioning_uri("ABCDEF", "alice@acme.com")

    assert uri.startswith("otpauth://totp/LoreMd%3Aalice%40acme.com?")
    assert "secret=ABCDEF" in uri
    assert "issuer=LoreMd" in uri
