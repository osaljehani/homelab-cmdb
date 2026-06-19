import typer
from rich.console import Console

from cmdb.db.session import get_session
from cmdb.domain.models import ImportSource
from cmdb.domain.services.collect import (
    CollectError,
    collect_docker,
    collect_facts,
    collect_k8s,
    collect_ports,
    collect_tailscale,
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


@app.command("k8s")
def collect_k8s_cmd(
    inventory: str = _INVENTORY,
    limit: str = _LIMIT,
) -> None:
    """Discover K8s clusters live (kubectl over SSH) and import nodes + namespaces."""
    try:
        with get_session() as session:
            log = collect_k8s(session, inventory, limit, ImportSource.COLLECT)
            clusters = log.k8s_clusters_upserted
            nodes = log.k8s_nodes_upserted
            namespaces = log.k8s_namespaces_upserted
            notes = log.notes
    except CollectError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]Collected:[/green] {clusters} clusters, {nodes} nodes, "
        f"{namespaces} namespaces"
    )
    if notes:
        console.print(f"[yellow]Warnings:[/yellow]\n{notes}")


@app.command("tailscale")
def collect_tailscale_cmd(
    inventory: str = _INVENTORY,
    limit: str = _LIMIT,
) -> None:
    """Gather Tailscale state live (status/serve) and import it."""
    try:
        with get_session() as session:
            log = collect_tailscale(session, inventory, limit, ImportSource.COLLECT)
            hosts = log.hosts_upserted
            services = log.tailscale_services_upserted
            notes = log.notes
    except CollectError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]Collected:[/green] {hosts} hosts, {services} exposed services"
    )
    if notes:
        console.print(f"[yellow]Warnings:[/yellow]\n{notes}")


@app.command("ports")
def collect_ports_cmd(
    inventory: str = _INVENTORY,
    limit: str = _LIMIT,
) -> None:
    """Gather `ss -tulpn` live and import listening ports."""
    try:
        with get_session() as session:
            log = collect_ports(session, inventory, limit, ImportSource.COLLECT)
            ports, notes = log.listening_ports_upserted, log.notes
    except CollectError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]Collected:[/green] {ports} listening ports")
    if notes:
        console.print(f"[yellow]Warnings:[/yellow]\n{notes}")


@app.command("all")
def collect_all_cmd(
    inventory: str = _INVENTORY,
    limit: str = _LIMIT,
) -> None:
    """Gather facts, Docker containers, K8s clusters, Tailscale state, and listening ports in one pass."""
    try:
        with get_session() as session:
            facts_log = collect_facts(session, inventory, limit, ImportSource.COLLECT)
            f_up, f_fail = facts_log.hosts_upserted, facts_log.hosts_failed
            f_notes = facts_log.notes
        with get_session() as session:
            docker_log = collect_docker(session, inventory, limit, ImportSource.COLLECT)
            d_count, d_notes = docker_log.containers_upserted, docker_log.notes
        with get_session() as session:
            k8s_log = collect_k8s(session, inventory, limit, ImportSource.COLLECT)
            k_clusters, k_nodes = k8s_log.k8s_clusters_upserted, k8s_log.k8s_nodes_upserted
            k_notes = k8s_log.notes
        with get_session() as session:
            ts_log = collect_tailscale(session, inventory, limit, ImportSource.COLLECT)
            ts_services, ts_notes = ts_log.tailscale_services_upserted, ts_log.notes
        with get_session() as session:
            ports_log = collect_ports(session, inventory, limit, ImportSource.COLLECT)
            ports_count, ports_notes = ports_log.listening_ports_upserted, ports_log.notes
    except CollectError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]Collected:[/green] {f_up} hosts ({f_fail} failed), {d_count} containers, "
        f"{k_clusters} clusters ({k_nodes} nodes), {ts_services} ts-services, "
        f"{ports_count} ports"
    )
    for label, notes in (("facts", f_notes), ("docker", d_notes), ("k8s", k_notes),
                         ("tailscale", ts_notes), ("ports", ports_notes)):
        if notes:
            console.print(f"[yellow]{label} warnings:[/yellow]\n{notes}")
