"""The gate.

Pure ASGI rather than BaseHTTPMiddleware, and that is a requirement rather than
a preference. BaseHTTPMiddleware buffers through an anyio stream and rewraps the
response, which is exactly the wrong thing to do to the MCP sub-app's streaming
endpoint; a plain ASGI callable can hand an exempt request straight through with
`await self.app(scope, receive, send)` and touch nothing at all.

WHAT MUST NEVER BE GATED, AND WHY

``/mcp`` and ``/.well-known/oauth-protected-resource/mcp`` are exempt from the
Authentik proxy provider that guards the rest of the deployment
(``skip_path_regex`` in the outpost's blueprint), because a proxy answers an
unauthenticated request with a 302 to an interactive login page and a cloud AI
client cannot complete one. The bearer checks in ``cmdb/mcp/auth.py`` are the
only boundary on that path.

Starlette middleware wraps *every* route, including the absolute ones
:func:`cmdb.mcp.server.attach_remote_mcp` appends to the router. So without an
explicit carve-out this gate would replace that 401-plus-``WWW-Authenticate``
token challenge with a 302 to ``/login`` -- reintroducing, inside the
application, the precise failure the outpost carve-out exists to avoid, and
breaking the live connector.

The exempt set is therefore **derived from the routes that were actually
registered** (``name`` starting with ``mcp_``) and passed in, never written out
a second time here. It is empty when the remote endpoint is disabled, so nothing
is exempted that does not exist.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from urllib.parse import quote

from cmdb.config import settings
from cmdb.web.auth import proxy, session as session_mod
from cmdb.web.auth.principal import Principal

# Reachable without a principal: the login machinery itself, and assets the
# login page needs in order to render.
PUBLIC_PATHS = frozenset({"/login", "/logout", "/setup"})
PUBLIC_PREFIXES = ("/static/", "/auth/")

API_PREFIX = "/api/"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def safe_next(target: str | None) -> str:
    """Reduce a ``?next=`` value to a local path, or ``/``.

    Without this, ``/login?next=https://evil.example`` turns the login page into
    an open redirect: the victim authenticates for real and is then bounced
    somewhere else, carrying the trust of having just logged in successfully.
    ``//host`` and ``/\\host`` are both protocol-relative in browsers, so a
    leading-slash test alone is not enough.
    """
    if not target or not target.startswith("/"):
        return "/"
    if target.startswith("//") or target.startswith("/\\"):
        return "/"
    return target


def authorized(principal: Principal) -> bool:
    """Apply CMDB_AUTH_REQUIRED_GROUPS.

    The check applies to identities that *come with* groups -- ``proxy`` and
    ``oidc`` -- and not to local accounts, which have none and would therefore
    be denied unconditionally by a non-empty requirement. That is not a bypass:
    there is no self-registration, so every local row was created deliberately
    by an operator with `cmdb users add` or the one-shot /setup page. It is what
    lets `local,oidc` mean "group-gated SSO, plus a break-glass password".
    """
    required = settings.auth_required_groups_set
    if not required or principal.source == "local":
        return True
    return required.issubset(principal.groups)


def resolve_principal(scope, headers: Headers) -> Principal | None:
    """Identify the caller, by whichever door the current mode opens."""
    modes = settings.auth_modes_set
    if "proxy" in modes:
        return proxy.principal_from_headers(headers)
    # A sessionless mode mounts no SessionMiddleware, so `session` may be absent
    # from the scope entirely rather than merely empty.
    return session_mod.principal_from_session(scope.get("session") or {})


def _same_origin(headers: Headers) -> bool:
    """Reject a cross-origin unsafe request.

    The session cookie is new CSRF surface: before it there were no cookies, so
    there was nothing for a cross-site form POST to ride on. SameSite=Lax blocks
    that, and this is the belt-and-braces half.

    Comparing Origin against the Host header rather than a configured URL keeps
    it correct both direct and behind the outpost, which rewrites Host to the
    public name and sets X-Forwarded-Proto -- either scheme matches. A request
    with no Origin is allowed: that is a non-browser client (curl, the CLI), and
    a browser always sends one on a cross-origin request.
    """
    origin = headers.get("origin")
    if not origin:
        return True
    host = headers.get("host")
    if not host:
        return False
    return origin in {f"http://{host}", f"https://{host}"}


class AuthMiddleware:
    def __init__(self, app, *, mcp_paths: frozenset[str] = frozenset()) -> None:
        self.app = app
        self.mcp_paths = mcp_paths

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        # First, and before anything else can go wrong: hand the MCP routes
        # through completely untouched.
        if path in self.mcp_paths:
            return await self.app(scope, receive, send)

        if not settings.auth_enabled:
            return await self.app(scope, receive, send)

        headers = Headers(scope=scope)
        method = scope.get("method", "GET").upper()

        # Checked before the public-path passthrough, so POST /login is covered.
        if method in UNSAFE_METHODS and not _same_origin(headers):
            return await PlainTextResponse("Cross-origin request refused", 403)(
                scope, receive, send
            )

        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await self.app(scope, receive, send)

        principal = resolve_principal(scope, headers)
        if principal is None:
            return await self._challenge(scope, receive, send, path)
        if not authorized(principal):
            return await PlainTextResponse(
                "Your account is not a member of a group permitted to use this "
                "instance.",
                403,
            )(scope, receive, send)

        scope.setdefault("state", {})["principal"] = principal
        return await self.app(scope, receive, send)

    async def _challenge(self, scope, receive, send, path: str) -> None:
        """401 for API clients, 302 for browsers.

        The redirect is what makes ``static/js/session.js`` keep working: it
        probes ``/healthz`` with ``redirect: 'manual'`` and reads the first hop's
        ``opaqueredirect`` as the lapse signal. A same-origin 302 to /login
        produces exactly that, so the stale-session banner behaves identically
        whether the outpost or this gate issued the redirect.
        """
        if path.startswith(API_PREFIX):
            return await JSONResponse({"detail": "Not authenticated"}, 401)(
                scope, receive, send
            )
        query = scope.get("query_string", b"").decode("latin-1")
        target = f"{path}?{query}" if query else path
        location = f"/login?next={quote(safe_next(target), safe='')}"
        return await RedirectResponse(location, status_code=302)(scope, receive, send)
