import sys
import typer
from pathlib import Path
from rich.console import Console

from cmdb.db.session import get_session
from cmdb.domain.services.generate import (
    generate_inventory_ini, generate_inventory_yaml, generate_ssh_config
)

app = typer.Typer(help="Generate config files from inventory", no_args_is_help=True)
console = Console()


@app.command("inventory")
def inventory(
    fmt: str = typer.Option("yaml", "--format", "-f", help="yaml or ini"),
    out: str | None = typer.Option(None, "--out", "-o", help="Output file (default: stdout)"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
) -> None:
    """Generate Ansible inventory."""
    if fmt not in ("yaml", "ini"):
        console.print(f"[red]Unknown format '{fmt}'. Use 'yaml' or 'ini'.[/red]")
        raise typer.Exit(1)
    with get_session() as session:
        content = (
            generate_inventory_ini(session, tag=tag)
            if fmt == "ini"
            else generate_inventory_yaml(session, tag=tag)
        )
    if out:
        Path(out).write_text(content)
        console.print(f"[green]Written to {out}[/green]")
    else:
        sys.stdout.write(content)


@app.command("ssh-config")
def ssh_config(
    out: str | None = typer.Option(None, "--out", "-o", help="Output file (default: stdout)"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
) -> None:
    """Generate SSH config blocks."""
    with get_session() as session:
        content = generate_ssh_config(session, tag=tag)
    if out:
        Path(out).write_text(content)
        console.print(f"[green]Written to {out}[/green]")
    else:
        sys.stdout.write(content)
