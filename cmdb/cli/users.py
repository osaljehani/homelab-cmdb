"""`cmdb users` -- manage web UI accounts.

This is the bootstrap path and the recovery path. There is no self-registration,
and in a deployment that normally signs in through an identity provider this is
also how you get in when the IdP is down.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from cmdb.db.session import get_session
from cmdb.domain.services.users import (
    create_user,
    delete_user,
    list_users,
    set_active,
    set_admin,
    set_password,
)

app = typer.Typer(help="Manage web UI accounts", no_args_is_help=True)
console = Console()


def _fail(message: str) -> None:
    """Report a refusal as a message, not a traceback."""
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


@app.command("add")
def add_cmd(
    username: str = typer.Argument(..., help="Login name"),
    email: str | None = typer.Option(None, "--email", help="Email address"),
    admin: bool = typer.Option(False, "--admin", help="Grant admin"),
    password: str | None = typer.Option(
        None,
        "--password",
        help="Password (omit to be prompted; a prompt keeps it out of shell history)",
    ),
) -> None:
    """Create an account."""
    if password is None:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    with get_session() as session:
        try:
            user = create_user(
                session, username, password=password, email=email, is_admin=admin
            )
        except ValueError as exc:
            _fail(str(exc))
        console.print(
            f"[green]Created[/green] {user.username}"
            + (" [yellow](admin)[/yellow]" if user.is_admin else "")
        )


@app.command("list")
def list_cmd() -> None:
    """List accounts."""
    table = Table(title="Users")
    table.add_column("Username", style="cyan")
    table.add_column("Email")
    table.add_column("Admin")
    table.add_column("Active")
    table.add_column("Login")
    table.add_column("Last seen")

    with get_session() as session:
        users = list_users(session)
        table.title = f"Users ({len(users)})"
        for user in users:
            if user.password_hash and user.oidc_subject:
                login = "password + sso"
            elif user.oidc_subject:
                login = "sso"
            elif user.password_hash:
                login = "password"
            else:
                login = "[red]none[/red]"
            table.add_row(
                user.username,
                user.email or "-",
                "yes" if user.is_admin else "-",
                "yes" if user.is_active else "[red]no[/red]",
                login,
                user.last_login_at.strftime("%Y-%m-%d %H:%M")
                if user.last_login_at
                else "never",
            )
    console.print(table)


@app.command("passwd")
def passwd_cmd(
    username: str = typer.Argument(..., help="Login name"),
    password: str | None = typer.Option(None, "--password", help="New password"),
) -> None:
    """Change an account's password."""
    if password is None:
        password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    with get_session() as session:
        try:
            set_password(session, username, password)
        except ValueError as exc:
            _fail(str(exc))
    console.print(f"[green]Password updated[/green] for {username}")


@app.command("rm")
def rm_cmd(
    username: str = typer.Argument(..., help="Login name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
) -> None:
    """Delete an account."""
    if not yes:
        typer.confirm(f"Delete user '{username}'?", abort=True)
    with get_session() as session:
        try:
            removed = delete_user(session, username)
        except ValueError as exc:
            _fail(str(exc))
    if not removed:
        _fail(f"no such user '{username}'")
    console.print(f"[green]Deleted[/green] {username}")


@app.command("promote")
def promote_cmd(username: str = typer.Argument(..., help="Login name")) -> None:
    """Grant admin."""
    with get_session() as session:
        try:
            set_admin(session, username, True)
        except ValueError as exc:
            _fail(str(exc))
    console.print(f"[green]{username} is now an admin[/green]")


@app.command("demote")
def demote_cmd(username: str = typer.Argument(..., help="Login name")) -> None:
    """Revoke admin."""
    with get_session() as session:
        try:
            set_admin(session, username, False)
        except ValueError as exc:
            _fail(str(exc))
    console.print(f"[green]{username} is no longer an admin[/green]")


@app.command("disable")
def disable_cmd(username: str = typer.Argument(..., help="Login name")) -> None:
    """Deactivate an account without deleting it.

    Takes effect on the account's next request: the principal is resolved against
    the database every time, so an existing session stops working immediately.
    """
    with get_session() as session:
        try:
            set_active(session, username, False)
        except ValueError as exc:
            _fail(str(exc))
    console.print(f"[green]{username} disabled[/green]")


@app.command("enable")
def enable_cmd(username: str = typer.Argument(..., help="Login name")) -> None:
    """Reactivate an account."""
    with get_session() as session:
        try:
            set_active(session, username, True)
        except ValueError as exc:
            _fail(str(exc))
    console.print(f"[green]{username} enabled[/green]")
