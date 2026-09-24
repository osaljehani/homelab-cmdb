"""Login, logout, and the one-shot first-run setup page."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from cmdb.config import settings
from cmdb.domain.models import User
from cmdb.web.auth import throttle
from cmdb.web.auth.middleware import safe_next
from cmdb.web.auth.passwords import verify_password
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
def login_page(request: Request, next: str = "/"):
    # In proxy mode the reverse proxy owns the login experience entirely; there
    # is nothing for this page to offer.
    if not settings.session_enabled:
        return RedirectResponse("/", status_code=302)
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

    def failed(message: str, status: int = 401):
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

    user = db.query(User).filter(User.username == username).one_or_none()
    # verify_password is called against the *stored* value even when the user
    # does not exist (None -> False), so a missing account and a wrong password
    # cost roughly the same and the response cannot be used to enumerate users.
    stored = user.password_hash if user else None
    if not verify_password(password, stored) or not user.is_active:
        throttle.record_failure(username)
        return failed("Incorrect username or password.")

    throttle.record_success(username)
    user.last_login_at = datetime.utcnow()
    db.add(user)
    start_session(request, user)
    return RedirectResponse(target, status_code=302)


@router.post("/logout", include_in_schema=False)
def logout(request: Request):
    """Ends the app's own session.

    In proxy mode the app has no session to end -- the outpost holds it -- so
    this hands off to the outpost's sign-out endpoint instead. Anything else
    would drop the user back on a page the proxy still considers authenticated.
    """
    if not settings.session_enabled:
        return RedirectResponse("/outpost.goauthentik.io/sign_out", status_code=302)
    end_session(request)
    return RedirectResponse("/login", status_code=302)
