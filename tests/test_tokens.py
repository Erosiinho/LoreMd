from __future__ import annotations

from loremd.auth.tokens import generate_token, hash_token, parse_token, verify


def test_le_token_suit_le_format_annonce() -> None:
    parts, _ = generate_token()

    assert parts.raw.startswith("lmd_")
    assert parts.raw.count("_") >= 2
    assert len(parts.prefix) == 8


def test_le_hachage_verifie_le_token_complet() -> None:
    parts, digest = generate_token()

    assert verify(parts.raw, digest)
    assert not verify(parts.raw + "x", digest)
    assert digest != parts.raw


def test_le_secret_n_est_pas_derivable_du_prefixe() -> None:
    parts, digest = generate_token()

    assert parts.prefix not in digest
    assert hash_token(parts.prefix) != digest


def test_parse_token_accepte_un_secret_contenant_des_tirets_bas() -> None:
    parsed = parse_token("lmd_abcd1234_secret_avec_underscores")

    assert parsed is not None
    assert parsed.prefix == "abcd1234"
    assert parsed.secret == "secret_avec_underscores"


def test_parse_token_rejette_les_formats_inconnus() -> None:
    assert parse_token("Bearer quelquechose") is None
    assert parse_token("lmd_sansecret") is None
    assert parse_token("autre_abcd1234_secret") is None
