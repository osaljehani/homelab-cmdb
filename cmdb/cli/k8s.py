import typer
from rich.console import Console
from rich.table import Table

from cmdb.db.session import get_session
from cmdb.domain.models import K8sNodeRole
from cmdb.domain.services.k8s import (
    add_cluster, delete_cluster, list_clusters,
    add_node, list_nodes, remove_node
)

app = typer.Typer(help="Manage Kubernetes topology", no_args_is_help=True)
cluster_app = typer.Typer(help="Manage clusters", no_args_is_help=True)
node_app = typer.Typer(help="Manage cluster nodes", no_args_is_help=True)
app.add_typer(cluster_app, name="cluster")
app.add_typer(node_app, name="node")
console = Console()


@cluster_app.command("add")
def cluster_add(
    name: str,
    description: str = typer.Option("", "--description", "-d"),
) -> None:
    with get_session() as session:
        add_cluster(session, name, description or None)
    console.print(f"[green]Created cluster '{name}'[/green]")


@cluster_app.command("list")
def cluster_list() -> None:
    table = Table(title="K8s Clusters")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Nodes")
    with get_session() as session:
        clusters = list_clusters(session)
        rows = [(c.name, c.description or "", str(len(c.nodes))) for c in clusters]
    for row in rows:
        table.add_row(*row)
    console.print(table)


@cluster_app.command("delete")
def cluster_delete(name: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    if not yes:
        typer.confirm(f"Delete cluster '{name}' and all its node associations?", abort=True)
    with get_session() as session:
        ok = delete_cluster(session, name)
    if ok:
        console.print(f"[green]Deleted cluster '{name}'[/green]")
    else:
        console.print(f"[red]Cluster '{name}' not found[/red]")
        raise typer.Exit(1)


@node_app.command("add")
def node_add(
    hostname: str,
    cluster: str,
    role: K8sNodeRole = typer.Option(..., "--role", help="control-plane, worker, or etcd"),
) -> None:
    with get_session() as session:
        add_node(session, hostname, cluster, role)
    console.print(f"[green]Added '{hostname}' to '{cluster}' as {role.value}[/green]")


@node_app.command("list")
def node_list(cluster: str) -> None:
    table = Table(title=f"Nodes in {cluster}")
    table.add_column("Hostname", style="cyan")
    table.add_column("Role")
    table.add_column("IP")
    with get_session() as session:
        nodes = list_nodes(session, cluster)
        rows = [(n.host.hostname, n.role.value, n.host.primary_ipv4 or "") for n in nodes]
    for row in rows:
        table.add_row(*row)
    console.print(table)


@node_app.command("remove")
def node_remove(hostname: str, cluster: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    if not yes:
        typer.confirm(f"Remove '{hostname}' from cluster '{cluster}'?", abort=True)
    with get_session() as session:
        ok = remove_node(session, hostname, cluster)
    if ok:
        console.print(f"[green]Removed '{hostname}' from '{cluster}'[/green]")
    else:
        console.print("[red]Node not found[/red]")
        raise typer.Exit(1)
