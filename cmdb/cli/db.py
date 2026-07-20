import typer

app = typer.Typer(help="Database management")


@app.command("upgrade")
def upgrade() -> None:
    """Run pending Alembic migrations."""
    from cmdb.db import run_migrations

    run_migrations()
    typer.echo("Migrations applied.")


@app.command("backfill-vuln-snapshots")
def backfill_vuln_snapshots() -> None:
    """Reconstruct daily vuln snapshots from existing scan history.

    Escape hatch for batches of historical scan files imported after the
    snapshot migration ran; normal imports snapshot automatically.
    """
    from cmdb.db.session import get_session
    from cmdb.domain.services.vuln_snapshots import backfill_snapshots

    with get_session() as session:
        written = backfill_snapshots(session)
    typer.echo(f"Backfilled {written} snapshot rows.")
