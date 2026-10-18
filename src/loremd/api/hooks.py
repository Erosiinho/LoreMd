"""Push webhook: the remote tells LoreMd a human pushed, and a sync starts now.

The periodic watcher would get there anyway; the webhook only cuts the delay.
It is authenticated with the shared secret `sync.webhook_secret`, in whichever
form the forge sends it:

- GitHub, Gitea, Forgejo: `X-Hub-Signature-256: sha256=<HMAC of the body>`
- Gitea (older): `X-Gitea-Signature: <HMAC of the body>`
- GitLab: `X-Gitlab-Token: <secret>`
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..auth.principal import Principal
from ..core.service import Loremd
from ..errors import NotFound
from .context import client_ip


def signature_valid(secret: str, body: bytes, headers: Mapping[str, str]) -> bool:
    """Whether the request carries the shared secret, in any supported form."""
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    hub = headers.get("x-hub-signature-256", "")
    if hub.startswith("sha256="):
        return hmac.compare_digest(hub.removeprefix("sha256="), expected)
    gitea = headers.get("x-gitea-signature")
    if gitea:
        return hmac.compare_digest(gitea, expected)
    gitlab = headers.get("x-gitlab-token")
    if gitlab:
        return hmac.compare_digest(gitlab.encode("utf-8"), secret.encode("utf-8"))
    return False


def pushed_ref(body: bytes) -> str | None:
    """The branch a push event is about, when the payload says so."""
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return None
    ref = payload.get("ref") if isinstance(payload, dict) else None
    return ref if isinstance(ref, str) else None


def build_hooks_router(core: Loremd) -> APIRouter:
    router = APIRouter(prefix="/hooks", tags=["sync"])

    @router.post("/git/{slug}", status_code=202)
    async def git_push(slug: str, request: Request) -> JSONResponse:
        secret = core.settings.sync.webhook_secret
        if not secret:
            return JSONResponse({"error": "webhook disabled"}, status_code=404)
        body = await request.body()
        if not signature_valid(secret, body, request.headers):
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        try:
            space = await core.spaces.get(slug)
        except NotFound:
            return JSONResponse({"error": f"unknown space: {slug}"}, status_code=404)

        branch = f"refs/heads/{core.settings.spaces.default_branch}"
        ref = pushed_ref(body)
        if ref is not None and ref != branch:
            return JSONResponse({"status": "ignored", "reason": f"push to {ref}"})

        core.sync.request(space.slug)
        await core.audit.record(
            Principal(
                name="webhook",
                scopes=frozenset(),
                space_ids=frozenset(),
                ip=client_ip(request.scope),
            ),
            "webhook_push",
            target=space.slug,
            space_id=space.id,
            meta={"ref": ref},
        )
        return JSONResponse({"status": "scheduled", "space": space.slug}, status_code=202)

    return router
