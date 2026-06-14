import typer
from rich.console import Console

from cmdb.db.session import get_session
from cmdb.domain.models import ImportSource
from cmdb.domain.services.ansible import import_from_path
from cmdb.domain.services.docker_import import (
    import_from_path as docker_import_from_path,
)
from cmdb.domain.services.k8s_import import import_from_path as k8s_import_from_path

app = typer.Typer(help="Import data into CMDB")
console = Console()


@app.command("ansible")
def import_ansible(
    path: str = typer.Argument(
        ..., help="Path to ansible --tree output dir or single file"
    ),
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


@app.command("docker")
def import_docker(
    path: str = typer.Argument(
        ..., help="Path to docker-export.sh JSON output file or directory"
    ),
) -> None:
    """Import Docker containers from docker-export.sh JSON output."""
    with get_session() as session:
        log = docker_import_from_path(session, path, ImportSource.CLI)
        containers = log.containers_upserted
        notes = log.notes
    console.print(f"[green]Imported:[/green] {containers} containers")
    if notes:
        console.print(f"[yellow]Warnings:[/yellow]\n{notes}")


@app.command("k8s")
def import_k8s(
    path: str = typer.Argument(
        ..., help="Path to k8s-export.sh JSON output file or directory"
    ),
) -> None:
    """Import K8s cluster topology from k8s-export.sh JSON output."""
    with get_session() as session:
        log = k8s_import_from_path(session, path, ImportSource.CLI)
        clusters = log.k8s_clusters_upserted
        nodes = log.k8s_nodes_upserted
        namespaces = log.k8s_namespaces_upserted
        notes = log.notes
    console.print(
        f"[green]Imported:[/green] {clusters} clusters, {nodes} nodes, {namespaces} namespaces"
    )
    if notes:
        console.print(f"[yellow]Warnings:[/yellow]\n{notes}")
