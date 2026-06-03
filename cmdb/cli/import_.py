import typer
from rich.console import Console

from cmdb.db.session import get_session
from cmdb.domain.models import ImportSource
from cmdb.domain.services.ansible import import_from_path

app = typer.Typer(help="Import data into CMDB")
console = Console()


@app.command("ansible")
def import_ansible(
    path: str = typer.Argument(..., help="Path to ansible --tree output dir or single file"),
) -> None:
    """Import hosts from ansible setup module JSON output."""
    with get_session() as session:
        log = import_from_path(session, path, ImportSource.CLI)
        upserted = log.hosts_upserted
        failed = log.hosts_failed
        notes = log.notes
    console.print(f"[green]Imported:[/green] {upserted} upserted, {failed} failed")
    if notes:
        console.print(f"[yellow]Errors:[/yellow]\n{notes}")
