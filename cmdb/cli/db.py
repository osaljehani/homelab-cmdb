import typer
from pathlib import Path

app = typer.Typer(help="Database management")


@app.command("upgrade")
def upgrade() -> None:
    """Run pending Alembic migrations."""
    from alembic.config import Config
    from alembic import command

    cfg = Config(str(Path(__file__).parent.parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")
    typer.echo("Migrations applied.")
