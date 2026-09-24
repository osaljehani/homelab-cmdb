"""Login, logout, and the one-shot first-run setup page.

Thin over cmdb.domain.services.users, per the project's layering: the CLI and
these routes are two front ends onto one implementation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from cmdb.config import settings
from cmdb.domain.services.users import (
    MIN_PASSWORD_LENGTH,
    authenticate,
    count_users,
    create_user,
    record_login,
)
from cmdb.web.auth import throttle
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
