import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cmdb.db.session import get_session
from cmdb.domain.services.hosts import (
    add_tag,
    delete_host,
    get_host,
    list_hosts,
    remove_tag,
)
from cmdb.domain.services.history import host_history
from cmdb.domain.services.security import host_posture

app = typer.Typer(help="Manage hosts", no_args_is_help=True)
console = Console()


@app.command("list")
def list_cmd(
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    os: str | None = typer.Option(None, "--os", help="Filter by OS family"),
) -> None:
    """List all hosts."""
    table = Table(title="Hosts")
    table.add_column("Hostname", style="cyan")
    table.add_column("IP")
    table.add_column("OS")
    table.add_column("CPU")
    table.add_column("RAM (MB)")
    table.add_column("Tags")
    table.add_column("Security")

    with get_session() as session:
        hosts = list_hosts(session, tag=tag, os_family=os)
        table.title = f"Hosts ({len(hosts)})"
        for h in hosts:
            p = host_posture(h)
            security = p.mac if p.hardened else "[red]exposed[/red]"
            table.add_row(
                h.hostname,
                h.primary_ipv4 or "",
                f"{h.os_distribution or ''} {h.os_version or ''}".strip(),
                f"{h.cpu_cores or ''}c/{h.cpu_threads or ''}t",
                str(h.memory_mb or ""),
                ", ".join(t.name for t in h.tags),
                security,
            )
    console.print(table)


@app.command("show")
def show_cmd(hostname: str) -> None:
    """Show full details for a host."""
    with get_session() as session:
        host = get_host(session, hostname)
        if not host:
            console.print(f"[red]Host '{hostname}' not found[/red]")
            raise typer.Exit(1)
        tags_str = ", ".join(t.name for t in host.tags) or "none"
        posture = host_posture(host)
        if posture.hardened:
            posture_str = f"[green]hardened[/green] via {posture.mac}"
        else:
            posture_str = "[red]EXPOSED[/red] " + "; ".join(posture.issues)
        fips_str = "on" if host.fips else "off"
        content = (
            f"[bold]Hostname:[/bold]     {host.hostname}\n"
            f"[bold]FQDN:[/bold]         {host.fqdn or '-'}\n"
            f"[bold]Machine ID:[/bold]   {host.machine_id}\n"
            f"[bold]IP:[/bold]           {host.primary_ipv4 or '-'}\n"
            f"[bold]Gateway:[/bold]      {host.gateway or '-'}\n"
            f"[bold]MAC:[/bold]          {host.primary_mac or '-'}\n"
            f"[bold]OS:[/bold]           {host.os_distribution} {host.os_version} ({host.os_family})\n"
            f"[bold]Kernel:[/bold]       {host.kernel or '-'}\n"
            f"[bold]CPU:[/bold]          {host.cpu_model or '-'} ({host.cpu_cores}c/{host.cpu_threads}t)\n"
            f"[bold]RAM:[/bold]          {host.memory_mb} MB\n"
            f"[bold]Vendor:[/bold]       {host.system_vendor} {host.product_name}\n"
            f"[bold]Virt:[/bold]         {host.virt_type}/{host.virt_role}\n"
            f"[bold]Security:[/bold]     {posture_str}\n"
            f"[bold]AppArmor:[/bold]     {host.apparmor_status or '-'}\n"
            f"[bold]SELinux:[/bold]      {host.selinux_status or '-'}\n"
            f"[bold]FIPS:[/bold]         {fips_str}\n"
            f"[bold]Tags:[/bold]         {tags_str}\n"
            f"[bold]Last seen:[/bold]    {host.last_seen}"
        )
        host_name = host.hostname
    console.print(Panel(content, title=host_name))


@app.command("history")
def history_cmd(hostname: str) -> None:
    """Show the change history (field diffs) for a host."""
    with get_session() as session:
        host = get_host(session, hostname)
        if not host:
            console.print(f"[red]Host '{hostname}' not found[/red]")
            raise typer.Exit(1)
        timeline = host_history(session, host)
        if not timeline:
            console.print(f"[yellow]No history recorded for '{hostname}' yet[/yellow]")
            return
        for entry in timeline:
            when = entry["captured_at"].strftime("%Y-%m-%d %H:%M")
            if entry["initial"]:
                console.print(f"[dim]{when}[/dim]  initial import")
            else:
                console.print(f"[dim]{when}[/dim]")
                for field, old, new in entry["changes"]:
                    console.print(
                        f"  [cyan]{field}[/cyan]: [red]{old}[/red] → [green]{new}[/green]"
                    )


@app.command("tag")
def tag_cmd(hostname: str, tag: str) -> None:
    """Add a tag to a host."""
    with get_session() as session:
        add_tag(session, hostname, tag)
    console.print(f"[green]Tagged '{hostname}' with '{tag}'[/green]")


@app.command("untag")
def untag_cmd(hostname: str, tag: str) -> None:
    """Remove a tag from a host."""
    with get_session() as session:
        remove_tag(session, hostname, tag)
    console.print(f"[green]Removed tag '{tag}' from '{hostname}'[/green]")


@app.command("delete")
def delete_cmd(
    hostname: str,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a host from the CMDB."""
    if not yes:
        typer.confirm(f"Delete host '{hostname}'?", abort=True)
    with get_session() as session:
        deleted = delete_host(session, hostname)
    if deleted:
        console.print(f"[green]Deleted '{hostname}'[/green]")
    else:
        console.print(f"[red]Host '{hostname}' not found[/red]")
        raise typer.Exit(1)
