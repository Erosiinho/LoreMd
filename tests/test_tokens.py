from __future__ import annotations

from loremd.auth.tokens import generate_token, hash_token, parse_token, verify


def test_token_follows_the_documented_format() -> None:
    parts, _ = generate_token()

    assert parts.raw.startswith("lmd_")
    assert parts.raw.count("_") >= 2
    assert len(parts.prefix) == 8


def test_hash_verifies_the_full_token() -> None:
    parts, digest = generate_token()

    assert verify(parts.raw, digest)
    assert not verify(parts.raw + "x", digest)
    assert digest != parts.raw


def test_secret_cannot_be_derived_from_prefix() -> None:
    parts, digest = generate_token()

    assert parts.prefix not in digest
    assert hash_token(parts.prefix) != digest


def test_parse_token_accepts_a_secret_with_underscores() -> None:
    parsed = parse_token("lmd_abcd1234_secret_with_underscores")

    assert parsed is not None
    assert parsed.prefix == "abcd1234"
    assert parsed.secret == "secret_with_underscores"


def test_parse_token_rejects_unknown_formats() -> None:
    assert parse_token("Bearer something") is None
    assert parse_token("lmd_nosecret") is None
    assert parse_token("other_abcd1234_secret") is None
