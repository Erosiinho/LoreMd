"""Admin console under /admin: review queue, conflicts, verification, audit.

Server-rendered pages and plain HTML forms: no JavaScript, no front-end build,
and a Content-Security-Policy that allows nothing but the console's own
stylesheet. Memory content is written by agents, so it is untrusted and always
rendered escaped.

Every form carries the session's CSRF token on top of the SameSite=Strict
cookie, and every decision is a commit signed with the admin's email.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from importlib import resources
from typing import Any, cast
from urllib.parse import urlencode

import jinja2
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..auth.admins import AdminSession
from ..auth.principal import Principal
from ..core.review import Keep
from ..core.service import Loremd
from ..db.repositories import AuditRepository
from ..errors import AuthError, ForbiddenError, LoremdError, NotFound, RateLimited
from .context import client_ip

PREFIX = "/admin"
SESSION_COOKIE = "loremd_session"

_TEMPLATES = jinja2.Environment(
    loader=jinja2.PackageLoader("loremd.api", "templates"),
    autoescape=True,
    undefined=jinja2.StrictUndefined,
)
_STYLESHEET = resources.files("loremd.api").joinpath("static/console.css").read_text("utf-8")

_SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}

#: Messages shown after a redirect. Codes, never free text: a link must not be
#: able to make the console say anything.
NOTICES = {
    "approved": "Memory approved and published.",
    "rejected": "Memory rejected.",
    "verified": "Memory marked as verified.",
    "unverified": "Memory no longer marked as verified.",
    "resolved": "Conflict resolved.",
    "synced": "Sync scheduled.",
}

_ERROR_STATUS = {NotFound: 404, ForbiddenError: 403, AuthError: 401, RateLimited: 429}


def _status_for(error: LoremdError) -> int:
    for kind, status in _ERROR_STATUS.items():
        if isinstance(error, kind):
            return status
    return 409 if error.code == "conflict" else 400


def _secure_headers(response: Response) -> Response:
    response.headers.update(_SECURITY_HEADERS)
    return response


def _render(template: str, *, status_code: int = 200, **context: Any) -> HTMLResponse:
    context.setdefault("session", None)
    context.setdefault("notice", None)
    html = _TEMPLATES.get_template(template).render(prefix=PREFIX, **context)
    return HTMLResponse(html, status_code=status_code, headers=_SECURITY_HEADERS)


def _redirect(path: str, **query: str) -> RedirectResponse:
    target = f"{PREFIX}{path}" + (f"?{urlencode(query)}" if query else "")
    response = RedirectResponse(target, status_code=303)
    _secure_headers(response)
    return response


def _ip(request: Request) -> str | None:
    return client_ip(request.scope)


def build_console_router(core: Loremd) -> APIRouter:
    router = APIRouter(prefix=PREFIX, include_in_schema=False)
    secure_cookie = core.settings.server.public_url.startswith("https://")

    async def current(request: Request) -> AdminSession | None:
        return await core.admins.session(request.cookies.get(SESSION_COOKIE))

    Page = Callable[[Request, AdminSession], Awaitable[Response]]

    async def page(request: Request, handler: Page, *, admin_only: bool = False) -> Response:
        """Runs a page for a logged-in admin; anyone else goes to the login form."""
        session = await current(request)
        if session is None:
            return _redirect("/login")
        if admin_only and not session.is_admin:
            return _render(
                "error.html",
                status_code=403,
                session=session,
                message="This page is reserved to the admin role.",
            )
        try:
            return await handler(request, session)
        except LoremdError as error:
            return _render(
                "error.html",
                status_code=_status_for(error),
                session=session,
                message=f"{error.code}: {error}",
            )

    async def action(
        request: Request, handler: Callable[[dict[str, str], AdminSession], Awaitable[Response]]
    ) -> Response:
        """A form submission: same as a page, plus the CSRF check."""

        async def checked(request: Request, session: AdminSession) -> Response:
            form = {key: str(value) for key, value in (await request.form()).items()}
            if form.get("csrf_token") != session.csrf_token:
                return _render(
                    "error.html",
                    status_code=403,
                    session=session,
                    message="The form expired. Go back, reload the page and try again.",
                )
            return await handler(form, session)

        return await page(request, checked)

    def principal(request: Request, session: AdminSession) -> Principal:
        return session.principal(_ip(request))

    # -- assets and login ----------------------------------------------------

    @router.get("/static/console.css")
    async def stylesheet() -> Response:
        return _secure_headers(Response(_STYLESHEET, media_type="text/css"))

    @router.get("/login")
    async def login_form(request: Request) -> Response:
        if await current(request) is not None:
            return _redirect("")
        return _render("login.html", error=None, email="")

    @router.post("/login")
    async def login(request: Request) -> Response:
        form = await request.form()
        email = str(form.get("email", ""))
        try:
            raw, session = await core.admins.login(
                email, str(form.get("password", "")), str(form.get("code", "")), ip=_ip(request)
            )
        except (AuthError, RateLimited) as error:
            await core.audit.record(
                Principal(
                    name=email.strip().lower()[:200] or "anonymous",
                    scopes=frozenset(),
                    space_ids=frozenset(),
                    ip=_ip(request),
                ),
                "console_login_failed",
                meta={"reason": error.code},
            )
            return _render(
                "login.html", status_code=_status_for(error), error=str(error), email=email
            )
        await core.audit.record(session.principal(_ip(request)), "console_login")
        response = _redirect("")
        response.set_cookie(
            SESSION_COOKIE,
            raw,
            max_age=core.settings.console.session_hours * 3600,
            path=PREFIX,
            secure=secure_cookie,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        async def handler(form: dict[str, str], session: AdminSession) -> Response:
            await core.admins.logout(request.cookies.get(SESSION_COOKIE))
            response = _redirect("/login")
            response.delete_cookie(SESSION_COOKIE, path=PREFIX)
            return response

        return await action(request, handler)

    # -- dashboard -----------------------------------------------------------

    @router.get("")
    async def dashboard(request: Request) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            who = principal(request, session)
            return _render(
                "dashboard.html",
                session=session,
                notice=NOTICES.get(request.query_params.get("notice", "")),
                spaces=await core.spaces.list(),
                counts=await core.review.counts(who),
            )

        return await page(request, handler)

    @router.post("/spaces/{slug}/sync")
    async def sync_space(request: Request, slug: str) -> Response:
        async def handler(form: dict[str, str], session: AdminSession) -> Response:
            space = await core.spaces.get(slug)
            core.sync.request(space.slug)
            await core.audit.record(
                principal(request, session), "console_sync", target=slug, space_id=space.id
            )
            return _redirect("", notice="synced")

        return await action(request, handler)

    # -- review queue --------------------------------------------------------

    @router.get("/review")
    async def review(request: Request) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            space = request.query_params.get("space") or None
            return _render(
                "review.html",
                session=session,
                notice=NOTICES.get(request.query_params.get("notice", "")),
                space=space,
                spaces=await core.spaces.list(),
                memories=await core.review.queue(principal(request, session), space_slug=space),
            )

        return await page(request, handler)

    @router.post("/memories/{memory_id}/approve")
    async def approve(request: Request, memory_id: str) -> Response:
        async def handler(form: dict[str, str], session: AdminSession) -> Response:
            await core.review.approve(principal(request, session), memory_id)
            return _redirect("/review", notice="approved")

        return await action(request, handler)

    @router.post("/memories/{memory_id}/reject")
    async def reject(request: Request, memory_id: str) -> Response:
        async def handler(form: dict[str, str], session: AdminSession) -> Response:
            await core.review.reject(
                principal(request, session), memory_id, form.get("reason", "")
            )
            return _redirect("/review", notice="rejected")

        return await action(request, handler)

    @router.post("/memories/{memory_id}/verify")
    async def verify(request: Request, memory_id: str) -> Response:
        async def handler(form: dict[str, str], session: AdminSession) -> Response:
            verified = form.get("verified") == "1"
            await core.review.set_verified(principal(request, session), memory_id, verified)
            return _redirect(
                f"/memories/{memory_id}", notice="verified" if verified else "unverified"
            )

        return await action(request, handler)

    @router.get("/memories/{memory_id}")
    async def memory(request: Request, memory_id: str) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            who = principal(request, session)
            found = await core.memories.lookup(who, memory_id)
            resolved = await core.spaces.resolve(found.space, who)
            return _render(
                "memory.html",
                session=session,
                notice=NOTICES.get(request.query_params.get("notice", "")),
                memory=found,
                history=resolved.repo.history(found.path),
            )

        return await page(request, handler)

    # -- conflicts -----------------------------------------------------------

    @router.get("/conflicts")
    async def conflicts(request: Request) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            return _render(
                "conflicts.html",
                session=session,
                notice=NOTICES.get(request.query_params.get("notice", "")),
                conflicts=await core.review.conflicts(principal(request, session)),
            )

        return await page(request, handler)

    @router.get("/conflicts/{memory_id}")
    async def conflict(request: Request, memory_id: str) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            view = await core.review.conflict(principal(request, session), memory_id)
            return _render("conflict.html", session=session, view=view)

        return await page(request, handler)

    @router.post("/conflicts/{memory_id}/resolve")
    async def resolve(request: Request, memory_id: str) -> Response:
        async def handler(form: dict[str, str], session: AdminSession) -> Response:
            keep = form.get("keep", "")
            if keep not in ("memory", "copy", "discard"):
                return _render(
                    "error.html",
                    status_code=400,
                    session=session,
                    message="Pick which version to keep.",
                )
            await core.review.resolve(
                principal(request, session),
                memory_id,
                cast(Keep, keep),
                content=form.get("content") if keep != "discard" else None,
            )
            return _redirect("/conflicts", notice="resolved")

        return await action(request, handler)

    # -- audit ---------------------------------------------------------------

    @router.get("/audit")
    async def audit(request: Request) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            actor = request.query_params.get("actor") or None
            action_name = request.query_params.get("action") or None
            try:
                limit = max(1, min(int(request.query_params.get("limit", "100")), 1000))
            except ValueError:
                limit = 100
            async with core.db.acquire() as connection:
                entries = await AuditRepository(connection).tail(limit, actor, action_name)
            return _render(
                "audit.html",
                session=session,
                entries=entries,
                actor=actor or "",
                action=action_name or "",
                limit=limit,
            )

        return await page(request, handler, admin_only=True)

    return router

