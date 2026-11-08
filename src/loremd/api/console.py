"""Admin console under /admin: overview, spaces, sessions, review, conflicts, agents, audit.

Server-rendered pages and plain HTML forms: no JavaScript, no front-end build,
and a Content-Security-Policy that allows nothing but the console's own
stylesheet. Memory content is written by agents, so it is untrusted and always
rendered escaped.

Every form carries the session's CSRF token on top of the SameSite=Strict
cookie, and every decision is a commit signed with the admin's email.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from importlib import resources
from typing import Any, cast
from urllib.parse import quote, urlencode

import jinja2
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..auth.admins import AdminSession
from ..auth.principal import Principal
from ..core.browse import MemoryFilter, sections_of
from ..core.handoffs import SECTIONS
from ..core.models import MemoryStatus, MemoryType
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


def _ago(moment: datetime | None) -> str:
    """'3 h ago', for lists; the exact time goes in a tooltip."""
    if moment is None:
        return "never"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    seconds = (datetime.now(UTC) - moment).total_seconds()
    if seconds < 0:
        return "in the future"
    units = ((86400 * 365, "y"), (86400 * 30, "mo"), (86400, "d"), (3600, "h"), (60, "min"))
    for size, unit in units:
        if seconds >= size:
            return f"{int(seconds // size)} {unit} ago"
    return "just now"


def _stamp(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


_BULLET = re.compile(r"^(\s*)- \[( |x|X)\] |^(\s*)- ", re.MULTILINE)


def _bullets(text: str) -> str:
    """Markdown list markers as glyphs, for handoff sections shown as plain text."""

    def glyph(match: re.Match[str]) -> str:
        if match.group(2) is not None:
            return f"{match.group(1)}{'☑' if match.group(2) in 'xX' else '☐'} "
        return f"{match.group(3)}• "

    return _BULLET.sub(glyph, text)


_TEMPLATES.filters["ago"] = _ago
_TEMPLATES.filters["bullets"] = _bullets
_TEMPLATES.filters["stamp"] = _stamp
_TEMPLATES.globals.update(
    prefix=PREFIX,
    sections_of=sections_of,
    section_titles=SECTIONS,
)

#: Badge counts of the sidebar, filled in once per request by `page`.
_NAV: ContextVar[dict[str, int] | None] = ContextVar("loremd_console_nav", default=None)

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
    context.setdefault("nav", _NAV.get() or {"pending": 0, "conflicts": 0})
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
            counts = await core.review.counts(session.principal(_ip(request)))
            _NAV.set(
                {
                    "pending": sum(c.get("pending", 0) for c in counts.values()),
                    "conflicts": sum(c.get("conflict", 0) for c in counts.values()),
                }
            )
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
            # Secure follows the request, not public_url: the console is often
            # reached on a LAN port in plain http, where a Secure cookie would
            # be dropped and the login would loop back to the form.
            secure=request.url.scheme == "https",
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
            activity = None
            if session.is_admin:
                async with core.db.acquire() as connection:
                    activity = await AuditRepository(connection).tail(8)
            return _render(
                "dashboard.html",
                session=session,
                active="home",
                notice=NOTICES.get(request.query_params.get("notice", "")),
                overview=await core.browse.overview(who),
                spaces=await core.spaces.list(),
                counts=await core.review.counts(who),
                sessions=(await core.browse.sessions(who, limit=5)),
                activity=activity,
            )

        return await page(request, handler)

    # -- spaces --------------------------------------------------------------

    @router.get("/spaces")
    async def spaces(request: Request) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            who = principal(request, session)
            listed = await core.spaces.list()
            return _render(
                "spaces.html",
                session=session,
                active="spaces",
                notice=NOTICES.get(request.query_params.get("notice", "")),
                spaces=listed,
                counts=await core.review.counts(who),
                types={s.slug: await core.browse.type_counts(who, s.slug) for s in listed},
            )

        return await page(request, handler)

    @router.get("/spaces/{slug}")
    async def space(request: Request, slug: str) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            who = principal(request, session)
            found = await core.browse.space(who, slug)
            memory_filter = MemoryFilter.parse(request.query_params)
            try:
                page_number = max(1, int(request.query_params.get("page", "1")))
            except ValueError:
                page_number = 1
            counts = await core.review.counts(who)
            return _render(
                "space.html",
                session=session,
                active="spaces",
                notice=NOTICES.get(request.query_params.get("notice", "")),
                space=found,
                counts=counts.get(found.slug, {}),
                types=await core.browse.type_counts(who, slug),
                projects=await core.browse.projects(who, slug),
                filter=memory_filter,
                page=await core.browse.memories(who, slug, memory_filter, page=page_number),
                sessions=await core.browse.sessions(who, space_slug=slug, limit=50),
                memory_types=[t.value for t in MemoryType],
                statuses=[s.value for s in MemoryStatus],
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
            return _redirect(f"/spaces/{quote(space.slug)}", notice="synced")

        return await action(request, handler)

    # -- sessions ------------------------------------------------------------

    @router.get("/sessions")
    async def sessions(request: Request) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            space_slug = request.query_params.get("space") or None
            return _render(
                "sessions.html",
                session=session,
                active="sessions",
                space=space_slug,
                spaces=await core.spaces.list(),
                sessions=await core.browse.sessions(
                    principal(request, session), space_slug=space_slug
                ),
            )

        return await page(request, handler)

    @router.get("/sessions/thread")
    async def thread(request: Request) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            space_slug = request.query_params.get("space", "")
            project = request.query_params.get("project") or None
            return _render(
                "thread.html",
                session=session,
                active="sessions",
                space=space_slug,
                project=project,
                handoffs=await core.browse.thread(principal(request, session), space_slug, project),
            )

        return await page(request, handler)

    # -- agents and tokens -----------------------------------------------------

    @router.get("/agents")
    async def agents(request: Request) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            return _render(
                "agents.html",
                session=session,
                active="agents",
                agents=await core.browse.agents(principal(request, session)),
            )

        return await page(request, handler, admin_only=True)

    # -- review queue --------------------------------------------------------

    @router.get("/review")
    async def review(request: Request) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            space = request.query_params.get("space") or None
            return _render(
                "review.html",
                session=session,
                active="review",
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
                active="spaces",
                sections=sections_of(found) if found.type is MemoryType.HANDOFF else {},
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
                active="conflicts",
                notice=NOTICES.get(request.query_params.get("notice", "")),
                conflicts=await core.review.conflicts(principal(request, session)),
            )

        return await page(request, handler)

    @router.get("/conflicts/{memory_id}")
    async def conflict(request: Request, memory_id: str) -> Response:
        async def handler(request: Request, session: AdminSession) -> Response:
            view = await core.review.conflict(principal(request, session), memory_id)
            return _render("conflict.html", session=session, active="conflicts", view=view)

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
                active="audit",
                entries=entries,
                actor=actor or "",
                action=action_name or "",
                limit=limit,
            )

        return await page(request, handler, admin_only=True)

    return router
