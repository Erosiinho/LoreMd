"""Write validation: the gate every memory goes through before touching disk."""

from __future__ import annotations

import pytest

from loremd.core.memories import validate_content
from loremd.errors import SecretDetected, ValidationError


def test_content_is_stripped() -> None:
    assert validate_content("\n  We use Postgres.  \n", max_bytes=1000) == "We use Postgres."


def test_empty_content_is_refused() -> None:
    for content in ("", "   ", "\n\n"):
        with pytest.raises(ValidationError, match="empty"):
            validate_content(content, max_bytes=1000)


def test_oversized_content_is_refused() -> None:
    with pytest.raises(ValidationError, match="too long"):
        validate_content("x" * 101, max_bytes=100)


def test_limit_counts_bytes_not_characters() -> None:
    # Four characters, eight bytes in UTF-8.
    with pytest.raises(ValidationError, match="too long"):
        validate_content("éàüñ", max_bytes=7)

    assert validate_content("éàüñ", max_bytes=8) == "éàüñ"


def test_a_secret_stops_the_write() -> None:
    with pytest.raises(SecretDetected):
        validate_content(
            "Use key AKIAIOSFODNN7EXAMPLE for the backups.", max_bytes=1000
        )


def test_the_rejection_does_not_echo_the_secret() -> None:
    with pytest.raises(SecretDetected) as error:
        validate_content("AKIAIOSFODNN7EXAMPLE", max_bytes=1000)

    assert "AKIAIOSFODNN7EXAMPLE" not in str(error.value)


def test_detection_can_be_turned_off() -> None:
    content = "Use key AKIAIOSFODNN7EXAMPLE for the backups."

    assert validate_content(content, max_bytes=1000, detect_secrets=False) == content


def test_ordinary_decision_passes() -> None:
    content = "We use Postgres with Drizzle as the ORM.\nReason: end-to-end typing."

    assert validate_content(content, max_bytes=1000) == content
