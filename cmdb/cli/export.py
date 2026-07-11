import json
from pathlib import Path

import typer
from rich.console import Console

from cmdb.db.session import get_session
from cmdb.domain.services.export import export_all, restore_all

console = Console()


def export_cmd(
    path: Path | None = typer.Argument(None, help="Output file (default: stdout)"),
    fmt: str = typer.Option("json", "--format", "-f", help="json or yaml"),
) -> None:
    """Dump the whole CMDB to JSON/YAML.

    Portability and inspection — copying the SQLite file is the real backup.
    """
    if fmt not in ("json", "yaml"):
        raise typer.BadParameter("--format must be json or yaml")
    with get_session() as session:
        dump = export_all(session)
    if fmt == "yaml":
        import yaml

        text = yaml.safe_dump(dump, sort_keys=False)
    else:
        text = json.dumps(dump, indent=2)
    if path is None:
        typer.echo(text)
    else:
        path.write_text(text)
        console.print(f"[green]Wrote {path}[/green]")


def restore_cmd(
    path: Path = typer.Argument(..., help="Export file (.json / .yaml)"),
    force: bool = typer.Option(
        False, "--force", help="Wipe ALL current data before restoring"
    ),
) -> None:
    """Restore an export into an empty, migrated database."""
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if force:
        typer.confirm(
            "This deletes ALL current data before restoring. Continue?", abort=True
        )
    with get_session() as session:
        try:
            counts = restore_all(session, data, force=force)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
    total = sum(counts.values())
    tables = sum(1 for c in counts.values() if c)
    console.print(f"[green]Restored {total} rows across {tables} tables[/green]")
