from __future__ import annotations

import hashlib
import hmac

from loremd.api.hooks import pushed_ref, signature_valid

SECRET = "s3cret-webhook"
BODY = b'{"ref": "refs/heads/main"}'


def _hmac(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_github_signature_is_accepted() -> None:
    headers = {"x-hub-signature-256": f"sha256={_hmac(BODY)}"}

    assert signature_valid(SECRET, BODY, headers)


def test_a_signature_over_another_body_is_refused() -> None:
    headers = {"x-hub-signature-256": f"sha256={_hmac(b'{}')}"}

    assert not signature_valid(SECRET, BODY, headers)


def test_a_signature_with_another_secret_is_refused() -> None:
    headers = {"x-hub-signature-256": f"sha256={_hmac(BODY, 'other')}"}

    assert not signature_valid(SECRET, BODY, headers)


def test_gitea_signature_is_accepted() -> None:
    assert signature_valid(SECRET, BODY, {"x-gitea-signature": _hmac(BODY)})


def test_gitlab_token_is_accepted() -> None:
    assert signature_valid(SECRET, BODY, {"x-gitlab-token": SECRET})
    assert not signature_valid(SECRET, BODY, {"x-gitlab-token": "guess"})


def test_no_credential_is_refused() -> None:
    assert not signature_valid(SECRET, BODY, {})


def test_pushed_ref_reads_the_payload() -> None:
    assert pushed_ref(BODY) == "refs/heads/main"
    assert pushed_ref(b"not json") is None
    assert pushed_ref(b"[]") is None
    assert pushed_ref(b"") is None
