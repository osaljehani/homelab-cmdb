"""Login, logout, and the one-shot first-run setup page.

Thin over cmdb.domain.services.users, per the project's layering: the CLI and
these routes are two front ends onto one implementation.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from cmdb.config import settings
from cmdb.domain.services.users import (
    MIN_PASSWORD_LENGTH,
    authenticate,
    count_users,
    create_user,
    get_user_by_oidc_subject,
    get_user_by_username,
    link_oidc_subject,
    record_login,
)
from cmdb.web.auth import oidc, throttle
from cmdb.web.auth.middleware import safe_next
from cmdb.web.auth.session import end_session, start_session
from cmdb.web.deps import get_db_dep, templates

router = APIRouter()


def _login_context(request: Request, next_url: str, **extra) -> dict:
    modes = settings.auth_modes_set
    return {
        "local_enabled": "local" in modes,
        "oidc_enabled": "oidc" in modes,
        "oidc_display_name": settings.oidc_display_name,
        "next": next_url,
        **extra,
    }


@router.get("/login", include_in_schema=False)
def login_page(
    request: Request, next: str = "/", db: Session = Depends(get_db_dep)
):
    # In proxy mode the reverse proxy owns the login experience entirely; there
    # is nothing for this page to offer.
    if not settings.session_enabled:
        return RedirectResponse("/", status_code=302)
    # A fresh install has no account to log in as, so the form would be a dead
    # end. Send them to the one-shot setup page instead.
    if "local" in settings.auth_modes_set and count_users(db) == 0:
        return RedirectResponse("/setup", status_code=302)
    return templates.TemplateResponse(
        request, "auth/login.html", _login_context(request, safe_next(next))
    )


@router.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db_dep),
):
    target = safe_next(next)
    if "local" not in settings.auth_modes_set:
        return RedirectResponse("/login", status_code=302)

    def failed(message: str, status: int):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            _login_context(request, target, error=message, username=username),
            status_code=status,
        )

    remaining = throttle.locked_for(username)
    if remaining:
        return failed(
            f"Too many failed attempts. Try again in {int(remaining) + 1}s.", 429
        )

    user = authenticate(db, username, password)
    if user is None:
        throttle.record_failure(username)
        # One message for a wrong password, a missing account and an inactive
        # one: anything more specific makes this an enumeration oracle.
        return failed("Incorrect username or password.", 401)

    throttle.record_success(username)
    record_login(db, user)
    start_session(request, user)
    return RedirectResponse(target, status_code=302)


@router.post("/logout", include_in_schema=False)
def logout(request: Request):
    """Ends the app's own session.

    In proxy mode the app has no session to end -- the outpost holds it -- so this
    hands off to the outpost's sign-out endpoint instead. Anything else would drop
    the user back on a page the proxy still considers authenticated.
    """
    if not settings.session_enabled:
        return RedirectResponse("/outpost.goauthentik.io/sign_out", status_code=302)
    end_session(request)
    return RedirectResponse("/login", status_code=302)


def _setup_available(db: Session) -> None:
    """404 unless this really is a fresh, local-login-capable install.

    Closing on `count_users() == 0` is what stops /setup being a permanent
    account-creation hole -- the same shape as Authentik's initial-setup flow,
    which disables itself once it has run.
    """
    if "local" not in settings.auth_modes_set or count_users(db) > 0:
        raise HTTPException(status_code=404)


@router.get("/setup", include_in_schema=False)
def setup_page(request: Request, db: Session = Depends(get_db_dep)):
    _setup_available(db)
    return templates.TemplateResponse(
        request, "auth/setup.html", {"min_password_length": MIN_PASSWORD_LENGTH}
    )


@router.post("/setup", include_in_schema=False)
def setup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    email: str | None = Form(None),
    db: Session = Depends(get_db_dep),
):
    _setup_available(db)

    def failed(message: str):
        return templates.TemplateResponse(
            request,
            "auth/setup.html",
            {
                "min_password_length": MIN_PASSWORD_LENGTH,
                "error": message,
                "username": username,
                "email": email,
            },
            status_code=400,
        )

    if password != confirm:
        return failed("The two passwords do not match.")
    try:
        user = create_user(
            db, username, password=password, email=email or None, is_admin=True
        )
    except ValueError as exc:
        return failed(str(exc))

    # Setup authenticates them implicitly, so it counts as a login.
    record_login(db, user)
    start_session(request, user)
    return RedirectResponse("/", status_code=302)


# --- Federated login -------------------------------------------------------

# Namespaced in the session so a stray "state" key cannot collide with anything
# else, and so the in-flight values are easy to clear in one place.
_OIDC_STATE = "oidc_state"
_OIDC_NONCE = "oidc_nonce"
_OIDC_VERIFIER = "oidc_verifier"
_OIDC_NEXT = "oidc_next"


def _oidc_enabled() -> None:
    if "oidc" not in settings.auth_modes_set:
        raise HTTPException(status_code=404)


def _oidc_failed(request: Request, message: str, status: int = 401):
    """Render the login page with an error rather than a bare 500.

    A failed federated login is an ordinary outcome -- a cancelled consent, an
    expired code, a clock skew -- and the user needs a way back to the form.
    """
    for key in (_OIDC_STATE, _OIDC_NONCE, _OIDC_VERIFIER, _OIDC_NEXT):
        request.session.pop(key, None)
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        _login_context(request, "/", error=message),
        status_code=status,
    )


@router.get("/auth/oidc/login", include_in_schema=False)
async def oidc_login(request: Request, next: str = "/"):
    """Start the authorization code flow.

    state, nonce and the PKCE verifier are kept in the signed session rather than
    in a separate cookie: it already exists, it is already signed, and keeping
    them together means one place to clear when the flow ends.
    """
    _oidc_enabled()
    verifier = oidc.new_verifier()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    request.session[_OIDC_STATE] = state
    request.session[_OIDC_NONCE] = nonce
    request.session[_OIDC_VERIFIER] = verifier
    request.session[_OIDC_NEXT] = safe_next(next)

    try:
        url = await oidc.authorization_url(
            state=state, nonce=nonce, challenge=oidc.challenge_for(verifier)
        )
    except oidc.OidcError as exc:
        return _oidc_failed(request, f"Could not reach the identity provider: {exc}", 502)
    return RedirectResponse(url, status_code=302)


@router.get("/auth/oidc/callback", include_in_schema=False)
async def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db_dep),
):
    _oidc_enabled()

    expected_state = request.session.get(_OIDC_STATE)
    nonce = request.session.get(_OIDC_NONCE)
    verifier = request.session.get(_OIDC_VERIFIER)
    target = safe_next(request.session.get(_OIDC_NEXT) or "/")

    if error:
        return _oidc_failed(request, f"The identity provider refused the login: {error}")
    if not code or not state:
        return _oidc_failed(request, "The identity provider returned an incomplete response.")
    # compare_digest, and only after confirming a state was issued at all: without
    # this the callback is forgeable and the whole flow loses its CSRF defence.
    if not expected_state or not secrets.compare_digest(state, expected_state):
        return _oidc_failed(request, "This login request did not come from this browser.")
    if not nonce or not verifier:
        return _oidc_failed(request, "This login attempt has expired. Please try again.")

    try:
        tokens = await oidc.exchange_code(code, verifier)
        identity = await oidc.identity_from_tokens(tokens, nonce)
    except oidc.OidcError as exc:
        return _oidc_failed(request, f"Could not complete the login: {exc}")
    except Exception:
        # A malformed token, an unreachable JWKS, a rotated key. Never a 500 with
        # a traceback on a login page, and never the underlying detail.
        return _oidc_failed(request, "Could not verify the identity provider's response.")

    user = get_user_by_oidc_subject(db, identity.subject)
    if user is None:
        user = _link_or_provision(db, identity)
    if user is None:
        return _oidc_failed(
            request,
            f"No account here is linked to '{identity.username}'. Ask an "
            "administrator to create one with `cmdb users add`.",
            403,
        )
    if not user.is_active:
        return _oidc_failed(request, "That account is disabled.", 403)

    for key in (_OIDC_STATE, _OIDC_NONCE, _OIDC_VERIFIER, _OIDC_NEXT):
        request.session.pop(key, None)
    record_login(db, user)
    start_session(request, user, groups=identity.groups, source="oidc")
    return RedirectResponse(target, status_code=302)


def _link_or_provision(db: Session, identity) -> "object | None":
    """Bind a federated identity to a row, or create one if that is allowed.

    First federated login for an operator-created account links it, by username.
    A row already bound to a *different* subject is never re-bound: two identities
    must not be able to contend for one username.
    """
    existing = get_user_by_username(db, identity.username)
    if existing is not None:
        if existing.oidc_subject and existing.oidc_subject != identity.subject:
            return None
        return link_oidc_subject(db, existing, identity.subject)
    if not settings.oidc_auto_create_users:
        return None
    return create_user(
        db,
        identity.username,
        email=identity.email,
        oidc_subject=identity.subject,
    )
