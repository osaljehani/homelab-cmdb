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
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import typer

app = typer.Typer(help="Demo mode: one command, a populated fictional fleet.")


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
        help="Wipe any existing demo DB before seeding (default) or keep it",
    ),
    seed_only: bool = typer.Option(
        False, "--seed-only", help="Seed the demo DB and exit without serving"
    ),
) -> None:
    """Seed a fictional demo fleet and serve the web UI.

    Never touches a real database: the demo DB defaults to a tempdir path and
    is always driven via CMDB_DB_PATH in a child process, never in-process.
    """
    db_path = db if db is not None else _default_demo_db()

    if fresh:
        for candidate in (db_path, db_path.with_name(db_path.name + "-wal"),
                          db_path.with_name(db_path.name + "-shm")):
            candidate.unlink(missing_ok=True)

    env = {**os.environ, "CMDB_DB_PATH": str(db_path)}

    try:
        subprocess.run(
            [sys.executable, "-m", "cmdb.cli.main", "demo-seed"],
            env=env,
            check=True,
        )
    except subprocess.CalledProcessError:
        raise typer.Exit(1)

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

    Refuses to run against a database that already has hosts in it, so
    ``--keep`` on an already-seeded DB is a safe no-op rather than a
    duplicate-data footgun.
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
                "Use --fresh (the default) to wipe it first, or point --db elsewhere."
            )
            raise typer.Exit(1)
        seed(session)

    typer.echo("Demo data seeded.")
