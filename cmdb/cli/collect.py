import typer
from rich.console import Console

from cmdb.db.session import get_session
from cmdb.domain.models import ImportSource
from cmdb.domain.services.collect import (
    CollectError,
    collect_docker,
    collect_facts,
)

app = typer.Typer(help="Collect inventory on demand over SSH (via Ansible)")
console = Console()

_INVENTORY = typer.Option(
    None, "--inventory", "-i", help="Ansible inventory path (default: CMDB_ANSIBLE_INVENTORY)"
)
_LIMIT = typer.Option(None, "--limit", "-l", help="Limit to a host or group (default: all)")


@app.command("facts")
def collect_facts_cmd(
    inventory: str = _INVENTORY,
    limit: str = _LIMIT,
) -> None:
    """Gather Ansible facts live and import them."""
    try:
        with get_session() as session:
            log = collect_facts(session, inventory, limit, ImportSource.COLLECT)
            upserted, failed, notes = log.hosts_upserted, log.hosts_failed, log.notes
    except CollectError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]Collected:[/green] {upserted} upserted, {failed} failed")
    if notes:
        console.print(f"[yellow]Warnings:[/yellow]\n{notes}")


@app.command("docker")
def collect_docker_cmd(
    inventory: str = _INVENTORY,
    limit: str = _LIMIT,
) -> None:
    """Gather `docker ps` live and import containers."""
    try:
        with get_session() as session:
            log = collect_docker(session, inventory, limit, ImportSource.COLLECT)
            containers, notes = log.containers_upserted, log.notes
    except CollectError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]Collected:[/green] {containers} containers")
    if notes:
        console.print(f"[yellow]Warnings:[/yellow]\n{notes}")


@app.command("all")
def collect_all_cmd(
    inventory: str = _INVENTORY,
    limit: str = _LIMIT,
) -> None:
    """Gather facts then Docker containers in one pass."""
    try:
        with get_session() as session:
            facts_log = collect_facts(session, inventory, limit, ImportSource.COLLECT)
            f_up, f_fail = facts_log.hosts_upserted, facts_log.hosts_failed
            f_notes = facts_log.notes
        with get_session() as session:
            docker_log = collect_docker(session, inventory, limit, ImportSource.COLLECT)
            d_count, d_notes = docker_log.containers_upserted, docker_log.notes
    except CollectError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]Collected:[/green] {f_up} hosts ({f_fail} failed), {d_count} containers"
    )
    for label, notes in (("facts", f_notes), ("docker", d_notes)):
        if notes:
            console.print(f"[yellow]{label} warnings:[/yellow]\n{notes}")
