from __future__ import annotations

from loremd.auth import passwords


def test_a_password_verifies_against_its_hash() -> None:
    stored = passwords.hash_password("correct horse battery")

    assert stored.startswith("scrypt$")
    assert passwords.verify_password("correct horse battery", stored)
    assert not passwords.verify_password("correct horse batterY", stored)


def test_two_hashes_of_the_same_password_differ() -> None:
    assert passwords.hash_password("correct horse battery") != passwords.hash_password(
        "correct horse battery"
    )


def test_an_unknown_account_never_verifies() -> None:
    # None is what the login passes for an unknown email.
    assert not passwords.verify_password("anything at all", None)


def test_a_corrupted_hash_is_refused_rather_than_raising() -> None:
    assert not passwords.verify_password("anything", "scrypt$broken")
    assert not passwords.verify_password("anything", "bcrypt$1$2$3$aa$bb")


def test_weak_passwords_are_reported() -> None:
    assert passwords.check_strength("short") is not None
    assert passwords.check_strength("aaaaaaaaaaaaaaaa") is not None
    assert passwords.check_strength("correct horse battery") is None
