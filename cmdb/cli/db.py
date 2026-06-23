import typer

app = typer.Typer(help="Database management")


@app.command("upgrade")
def upgrade() -> None:
    """Run pending Alembic migrations."""
    from cmdb.db import run_migrations

    run_migrations()
    typer.echo("Migrations applied.")
