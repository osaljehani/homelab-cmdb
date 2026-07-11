"""`cmdb demo` — one-command demo mode with a populated fictional fleet.

Two commands live here:

- ``demo`` (public): resolves a demo DB path, optionally wipes it, seeds it in
  a child process, then execs into ``cmdb serve``.
- ``demo-seed`` (hidden): the child-process entrypoint. Runs migrations and
  the seeder against whatever DB ``CMDB_DB_PATH`` points at in its own
  environment.

The demo DB is deliberately never the default ``./cmdb.db`` — a user's real
database must be untouchable by accident. Seeding always happens in a
subprocess (not in-process) because ``cmdb/db/session.py`` binds its engine to
``settings.db_url`` at import time, and settings are instantiated the moment
``cmdb.config`` is imported — which happens as soon as this CLI starts up.

Wipe safety around ``--db``: the default tempdir DB is always fair game to
wipe in fresh mode (it's throwaway by construction). A user-supplied ``--db``
is a different story — if it already exists, fresh mode refuses to touch it
rather than silently deleting a file the user pointed us at (which could be a
real database). ``demo-seed`` signals "already seeded" with a distinct exit
code (3) so the parent can tell "already has data, reuse it" (fine in
``--keep`` mode) apart from a genuine seeding failure (exit 1, always fatal).
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import typer

app = typer.Typer(help="Demo mode: one command, a populated fictional fleet.")

# demo-seed exit code meaning "DB already has hosts" — distinct from generic
# failure (1) so the parent can treat it as reuse-ok in --keep mode.
ALREADY_SEEDED_EXIT_CODE = 3


def _default_demo_db() -> Path:
    return Path(tempfile.gettempdir()) / "cmdb-demo.db"


@app.command("demo")
def demo_cmd(
    port: int = typer.Option(8080, help="Port to serve the demo UI on"),
    host: str = typer.Option("127.0.0.1", help="Bind host for the demo UI"),
    db: Path | None = typer.Option(
        None, "--db", help="Demo DB path (default: <tempdir>/cmdb-demo.db)"
    ),
    fresh: bool = typer.Option(
        True,
        "--fresh/--keep",
        help=(
            "Wipe any existing demo DB before seeding (default), or keep it and reuse "
            "already-seeded data. A user-supplied --db that already exists is never wiped "
            "automatically, even with --fresh — see below."
        ),
    ),
    seed_only: bool = typer.Option(
        False, "--seed-only", help="Seed the demo DB and exit without serving"
    ),
) -> None:
    """Seed a fictional demo fleet and serve the web UI.

    Never touches a real database: the demo DB defaults to a tempdir path and
    is always driven via CMDB_DB_PATH in a child process, never in-process.

    Wipe behavior differs by DB source:

    \b
    - Default tempdir DB (no --db given): auto-wiped in fresh mode (default),
      since it's throwaway by construction.
    - User-supplied --db that already exists: fresh mode REFUSES to wipe it
      and exits with an error — delete it yourself first, or pass --keep to
      reuse it as-is.
    - User-supplied --db that doesn't exist yet: seeded fresh into the new
      file, no special handling needed.
    """
    db_path = db if db is not None else _default_demo_db()
    user_supplied_existing_db = db is not None and db_path.exists()

    if fresh:
        if user_supplied_existing_db:
            typer.echo(
                f"Refusing to wipe existing user-specified database: {db_path}\n"
                "Delete it yourself first if you want a fresh demo there, or pass "
                "--keep to reuse it as-is."
            )
            raise typer.Exit(1)
        for candidate in (db_path, db_path.with_name(db_path.name + "-wal"),
                          db_path.with_name(db_path.name + "-shm")):
            candidate.unlink(missing_ok=True)

    env = {**os.environ, "CMDB_DB_PATH": str(db_path)}

    result = subprocess.run(
        [sys.executable, "-m", "cmdb.cli.main", "demo-seed"],
        env=env,
    )
    if result.returncode == ALREADY_SEEDED_EXIT_CODE:
        if fresh:
            # Fresh mode should never see an already-seeded DB — the tempdir
            # path was just wiped above (or a user-supplied one that doesn't
            # exist yet was seeded fresh). Treat it as a genuine failure.
            raise typer.Exit(1)
        typer.echo(f"Demo database already seeded — reusing it: {db_path}")
    elif result.returncode != 0:
        raise typer.Exit(1)
    else:
        typer.echo(f"Demo database ready: {db_path}")

    if seed_only:
        return

    typer.echo(f"Starting demo UI at http://{host}:{port}")
    os.execvpe(
        sys.executable,
        [sys.executable, "-m", "cmdb.cli.main", "serve", "--host", host, "--port", str(port)],
        env,
    )


@app.command("demo-seed", hidden=True)
def demo_seed_cmd() -> None:
    """Internal: seed the DB pointed at by CMDB_DB_PATH. Not for direct use.

    Exits with ALREADY_SEEDED_EXIT_CODE (3) if the database already has
    hosts, so the parent ``demo`` command can distinguish "already seeded —
    fine to reuse in --keep mode" from a genuine seeding failure (exit 1).
    """
    from cmdb.db import run_migrations
    from cmdb.db.session import get_session
    from cmdb.demo.seed import seed
    from cmdb.domain.models import Host

    run_migrations()

    with get_session() as session:
        if session.query(Host).count() > 0:
            typer.echo(
                "Demo database already has hosts — refusing to reseed. "
                "Use --fresh (the default) to wipe it first, or --keep to reuse it."
            )
            raise typer.Exit(ALREADY_SEEDED_EXIT_CODE)
        seed(session)

    typer.echo("Demo data seeded.")
