"""Web UI accounts.

The business logic for local and federated logins, so that the CLI
(`cmdb users`), the first-run /setup page and the OIDC callback all go through
one implementation rather than three that drift.

There is no self-registration anywhere in this application. A row gets here by
`cmdb users add`, by the one-shot /setup page on a fresh install, or by an OIDC
callback linking a subject to a row an operator already created.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from cmdb.domain.models import User
from cmdb.web.auth.passwords import hash_password, verify_password

# Long enough to be worth the scrypt cost, short enough not to push people
# toward reuse. Deliberately a length floor and nothing else: composition rules
# push users toward predictable substitutions without adding real entropy.
MIN_PASSWORD_LENGTH = 12


def count_users(session: Session) -> int:
    return session.query(User).count()


def list_users(session: Session) -> list[User]:
    return session.query(User).order_by(User.username).all()


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.query(User).filter(User.username == username).one_or_none()


def get_user_by_oidc_subject(session: Session, subject: str) -> User | None:
    return session.query(User).filter(User.oidc_subject == subject).one_or_none()


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )


def create_user(
    session: Session,
    username: str,
    *,
    password: str | None = None,
    email: str | None = None,
    is_admin: bool = False,
    oidc_subject: str | None = None,
) -> User:
    """Create an account. `password` is optional for a federated-only user.

    The duplicate check is explicit rather than left to the UNIQUE constraint so
    callers get a message they can show, not an IntegrityError.
    """
    username = username.strip()
    if not username:
        raise ValueError("username must not be empty")
    if get_user_by_username(session, username) is not None:
        raise ValueError(f"user '{username}' already exists")
    if password is not None:
        _validate_password(password)

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password) if password else None,
        oidc_subject=oidc_subject,
        is_admin=is_admin,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


def authenticate(session: Session, username: str, password: str) -> User | None:
    """Verify a local password. None on every failure, for any reason.

    verify_password is called even when the user does not exist so that a
    missing account and a wrong password cost about the same; the login route
    relies on that to avoid being a user-enumeration oracle. An empty password
    can never succeed, which matters because a federated row's password_hash is
    NULL.
    """
    user = get_user_by_username(session, username)
    if not password:
        return None
    if not verify_password(password, user.password_hash if user else None):
        return None
    if user is None or not user.is_active:
        return None
    return user


def record_login(session: Session, user: User) -> None:
    user.last_login_at = datetime.utcnow()
    session.add(user)
    session.commit()


def set_password(session: Session, username: str, password: str) -> User:
    user = get_user_by_username(session, username)
    if user is None:
        raise ValueError(f"no such user '{username}'")
    _validate_password(password)
    user.password_hash = hash_password(password)
    session.add(user)
    session.commit()
    return user


def _other_active_admin_exists(session: Session, user: User) -> bool:
    return (
        session.query(User)
        .filter(
            User.is_admin.is_(True),
            User.is_active.is_(True),
            User.id != user.id,
        )
        .count()
        > 0
    )


def _guard_last_admin(session: Session, user: User) -> None:
    """Refuse to remove the only way back in.

    Recovery from this would mean hand-editing SQLite, so it should take more
    than one command to arrange.
    """
    if user.is_admin and user.is_active and not _other_active_admin_exists(session, user):
        raise ValueError(
            f"'{user.username}' is the last active admin; create another admin first"
        )


def delete_user(session: Session, username: str) -> bool:
    """Delete an account. False if it was not there, so callers can report it."""
    user = get_user_by_username(session, username)
    if user is None:
        return False
    _guard_last_admin(session, user)
    session.delete(user)
    session.commit()
    return True


def set_admin(session: Session, username: str, is_admin: bool) -> User:
    user = get_user_by_username(session, username)
    if user is None:
        raise ValueError(f"no such user '{username}'")
    if not is_admin:
        _guard_last_admin(session, user)
    user.is_admin = is_admin
    session.add(user)
    session.commit()
    return user


def set_active(session: Session, username: str, is_active: bool) -> User:
    user = get_user_by_username(session, username)
    if user is None:
        raise ValueError(f"no such user '{username}'")
    if not is_active:
        _guard_last_admin(session, user)
    user.is_active = is_active
    session.add(user)
    session.commit()
    return user


def link_oidc_subject(session: Session, user: User, subject: str) -> User:
    user.oidc_subject = subject
    session.add(user)
    session.commit()
    return user
