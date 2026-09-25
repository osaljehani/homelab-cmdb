"""Signed-cookie sessions for the local and federated login paths.

The cookie is Starlette's ``SessionMiddleware`` rather than a hand-rolled HMAC.
Signed cookies are the sort of thing that is subtly wrong for a year -- expiry
not covered by the signature, a non-constant-time compare, a padding oracle in
the serializer -- and itsdangerous has had that scrutiny.

``proxy`` and ``none`` modes sign nothing and mount no cookie machinery at all.
"""

from __future__ import annotations

import time

from cmdb.config import settings
from cmdb.db.session import get_session
from cmdb.domain.models import User
from cmdb.web.auth.principal import Principal

# Kept short: the cookie is signed, not encrypted, so anything in here is
# readable by the user it belongs to. Only the row id and the auth time.
SESSION_USER_ID = "uid"
SESSION_AUTH_AT = "at"
SESSION_GROUPS = "grp"
SESSION_SOURCE = "src"


def session_cookie_kwargs() -> dict:
    """Arguments for SessionMiddleware.

    ``same_site="lax"`` is half the CSRF story (it blocks the cross-site form
    POST); the Origin check in the middleware is the other half. ``https_only``
    is configurable only so a plain-http localhost run still works -- on the
    live origin HSTS makes HTTPS the only possibility anyway.
    """
    return {
        "secret_key": settings.resolved_secret_key(),
        "session_cookie": settings.session_cookie_name,
        "max_age": settings.session_max_age,
        "same_site": "lax",
        "https_only": settings.session_cookie_secure,
    }


def start_session(
    request,
    user: User,
    *,
    groups: frozenset[str] = frozenset(),
    source: str = "local",
) -> None:
    """Record a successful login. Clears first, so a fixated session id cannot
    survive the privilege change.

    ``groups`` are the ones the identity provider asserted at this login. They
    live in the (signed) cookie because there is no way to re-ask the IdP on
    every request the way the MCP path re-checks its token. The consequence is
    worth knowing: a group removal takes effect at the next login or when the
    session expires, not instantly -- unlike `is_active`, which is read from the
    database every request. CMDB_SESSION_MAX_AGE bounds that staleness.
    """
    request.session.clear()
    request.session[SESSION_USER_ID] = user.id
    request.session[SESSION_AUTH_AT] = int(time.time())
    request.session[SESSION_SOURCE] = source
    if groups:
        request.session[SESSION_GROUPS] = sorted(groups)


def end_session(request) -> None:
    request.session.clear()


def principal_from_session(session: dict) -> Principal | None:
    """Resolve the cookie's user id against the database, every request.

    The DB lookup is not cached into the cookie on purpose. It costs one indexed
    SQLite read and it is what makes `cmdb users rm` and the `is_active` flag
    take effect immediately, rather than whenever the cookie happens to expire.
    That is the same reasoning as the per-request group check on the MCP path.
    """
    user_id = session.get(SESSION_USER_ID)
    if not user_id:
        return None
    with get_session() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return Principal(
            username=user.username,
            email=user.email,
            groups=frozenset(session.get(SESSION_GROUPS) or ()),
            is_admin=bool(user.is_admin),
            source=session.get(SESSION_SOURCE) or "local",
        )
