"""FastMCP server exposing the CMDB domain layer as MCP tools (stdio transport).

Each tool mirrors the CLI/Web pattern: open a session, call a domain service,
serialize the result to a Pydantic model *before* the session closes, and return
it. Domain services raise ``ValueError`` for not-found cases; those propagate as
MCP tool errors the client surfaces to the user.

``cmdb.domain.services.collect`` is intentionally not exposed — it spawns
Ansible-over-SSH subprocesses, which is slow and side-effectful.
"""

from mcp.server.fastmcp import FastMCP

from cmdb.db.session import get_session
from cmdb.domain.models import ImportSource, K8sNodeRole
from cmdb.domain.services import (
    ansible as ansible_svc,
    generate as generate_svc,
    history as history_svc,
    hosts as hosts_svc,
    images as images_svc,
    k8s as k8s_svc,
    security as security_svc,
)
from cmdb.mcp.schemas import (
    ChangeOut,
    HostDetailOut,
    HostHistoryEntry,
    HostOut,
    ImageDetailOut,
    ImageSummaryOut,
    K8sClusterOut,
    K8sNodeOut,
    PostureOut,
    PostureSummaryOut,
    VulnSummaryOut,
)

mcp = FastMCP("HomeLabCMDB")


def _require_host(session, hostname: str):
    host = hosts_svc.get_host(session, hostname)
    if host is None:
        raise ValueError(f"Host '{hostname}' not found")
    return host


# --- Hosts (read) ----------------------------------------------------------


@mcp.tool()
def list_hosts(tag: str | None = None, os_family: str | None = None) -> list[HostOut]:
    """List inventory hosts, optionally filtered by tag or OS family."""
    with get_session() as session:
        hosts = hosts_svc.list_hosts(session, tag=tag, os_family=os_family)
        return [HostOut.model_validate(h) for h in hosts]


@mcp.tool()
def get_host(hostname: str) -> HostDetailOut:
    """Get full detail for one host, including containers, listening ports, and
    Tailscale services. Raises if the host is not found."""
    with get_session() as session:
        host = _require_host(session, hostname)
        return HostDetailOut.model_validate(host)


# --- Hosts (write) ---------------------------------------------------------


@mcp.tool()
def add_tag(hostname: str, tag: str) -> HostOut:
    """Add a tag to a host (creating the tag if needed). Idempotent."""
    with get_session() as session:
        host = hosts_svc.add_tag(session, hostname, tag)
        session.flush()
        return HostOut.model_validate(host)


@mcp.tool()
def remove_tag(hostname: str, tag: str) -> HostOut:
    """Remove a tag from a host. No-op if the host lacks the tag."""
    with get_session() as session:
        host = hosts_svc.remove_tag(session, hostname, tag)
        session.flush()
        return HostOut.model_validate(host)


@mcp.tool()
def delete_host(hostname: str) -> bool:
    """Delete a host and its related records. Returns True if it existed."""
    with get_session() as session:
        return hosts_svc.delete_host(session, hostname)


# --- Security (read) -------------------------------------------------------


@mcp.tool()
def host_posture(hostname: str) -> PostureOut:
    """Evaluate one host's security posture (hardened/exposed, active MAC, FIPS)."""
    with get_session() as session:
        host = _require_host(session, hostname)
        p = security_svc.host_posture(host)
        return PostureOut(
            hostname=host.hostname,
            hardened=p.hardened,
            mac=p.mac,
            fips=p.fips,
            issues=list(p.issues),
        )


@mcp.tool()
def posture_summary() -> PostureSummaryOut:
    """Aggregate security posture across all hosts (counts + exposed hostnames)."""
    with get_session() as session:
        hosts = hosts_svc.list_hosts(session)
        s = security_svc.posture_summary(hosts)
        return PostureSummaryOut(
            total=s["total"],
            hardened=s["hardened"],
            exposed=s["exposed"],
            fips_on=s["fips_on"],
            exposed_hostnames=[h.hostname for h in s["exposed_hosts"]],
        )


# --- History (read) --------------------------------------------------------


@mcp.tool()
def host_history(hostname: str) -> list[HostHistoryEntry]:
    """Newest-first timeline of recorded changes for a host."""
    with get_session() as session:
        host = _require_host(session, hostname)
        timeline = history_svc.host_history(session, host)
        return [
            HostHistoryEntry(
                captured_at=entry["captured_at"],
                initial=entry["initial"],
                changes=[
                    ChangeOut(field=f, old=old, new=new)
                    for (f, old, new) in entry["changes"]
                ],
            )
            for entry in timeline
        ]


# --- Kubernetes (read) -----------------------------------------------------


@mcp.tool()
def list_clusters() -> list[K8sClusterOut]:
    """List all Kubernetes clusters with node counts and namespaces."""
    with get_session() as session:
        clusters = k8s_svc.list_clusters(session)
        return [
            K8sClusterOut(
                name=c.name,
                description=c.description,
                node_count=len(c.nodes),
                namespaces=[ns.name for ns in c.namespaces],
            )
            for c in clusters
        ]


@mcp.tool()
def list_nodes(cluster: str) -> list[K8sNodeOut]:
    """List the nodes (host + role) in a Kubernetes cluster."""
    with get_session() as session:
        nodes = k8s_svc.list_nodes(session, cluster)
        return [
            K8sNodeOut(hostname=n.host.hostname, role=n.role.value, cluster=cluster)
            for n in nodes
        ]


# --- Kubernetes (write) ----------------------------------------------------


@mcp.tool()
def add_cluster(name: str, description: str | None = None) -> K8sClusterOut:
    """Create a Kubernetes cluster."""
    with get_session() as session:
        c = k8s_svc.add_cluster(session, name, description)
        session.flush()
        return K8sClusterOut(
            name=c.name, description=c.description, node_count=0, namespaces=[]
        )


@mcp.tool()
def delete_cluster(name: str) -> bool:
    """Delete a Kubernetes cluster and its nodes/namespaces. Returns True if it existed."""
    with get_session() as session:
        return k8s_svc.delete_cluster(session, name)


@mcp.tool()
def add_node(hostname: str, cluster: str, role: str) -> K8sNodeOut:
    """Add a host to a cluster with a role. role must be one of:
    control-plane, worker, etcd. Updates the role if the node already exists."""
    try:
        role_enum = K8sNodeRole(role)
    except ValueError:
        allowed = ", ".join(r.value for r in K8sNodeRole)
        raise ValueError(f"Invalid role '{role}'. Must be one of: {allowed}")
    with get_session() as session:
        node = k8s_svc.add_node(session, hostname, cluster, role_enum)
        session.flush()
        return K8sNodeOut(
            hostname=node.host.hostname, role=node.role.value, cluster=cluster
        )


@mcp.tool()
def remove_node(hostname: str, cluster: str) -> bool:
    """Remove a host from a cluster. Returns True if the node existed."""
    with get_session() as session:
        return k8s_svc.remove_node(session, hostname, cluster)


# --- Inventory generation (read) -------------------------------------------


@mcp.tool()
def generate_inventory_yaml(
    tag: str | None = None, include_ssh_vars: bool = False
) -> str:
    """Generate an Ansible YAML inventory from the hosts in the CMDB."""
    with get_session() as session:
        return generate_svc.generate_inventory_yaml(
            session, tag=tag, include_ssh_vars=include_ssh_vars
        )


@mcp.tool()
def generate_inventory_ini(tag: str | None = None) -> str:
    """Generate an Ansible INI inventory from the hosts in the CMDB."""
    with get_session() as session:
        return generate_svc.generate_inventory_ini(session, tag=tag)


@mcp.tool()
def generate_ssh_config(tag: str | None = None) -> str:
    """Generate an OpenSSH client config from the hosts in the CMDB."""
    with get_session() as session:
        return generate_svc.generate_ssh_config(session, tag=tag)


# --- Imports (write, path-based) -------------------------------------------


@mcp.tool()
def import_ansible(path: str) -> dict:
    """Import Ansible facts from a JSON file or directory on the server's
    filesystem (e.g. the output of `ansible -m setup --tree`). Returns a summary
    of hosts upserted/failed."""
    with get_session() as session:
        log = ansible_svc.import_from_path(session, path, ImportSource.CLI)
        session.flush()
        return {
            "hosts_upserted": log.hosts_upserted,
            "hosts_failed": log.hosts_failed,
            "notes": log.notes,
        }


# --- Image vulnerabilities --------------------------------------------------


def _image_summary(session, image) -> ImageSummaryOut:
    scan = images_svc.latest_scan(session, image)
    return ImageSummaryOut(
        ref=image.ref,
        expected_noisy=image.expected_noisy,
        digest=image.digest,
        last_scanned_at=image.last_scanned_at,
        critical=scan.critical if scan else 0,
        high=scan.high if scan else 0,
        medium=scan.medium if scan else 0,
        low=scan.low if scan else 0,
        total=scan.total if scan else 0,
    )


@mcp.tool()
def list_image_scans() -> list[ImageSummaryOut]:
    """List scanned container images with their latest severity counts."""
    with get_session() as session:
        return [_image_summary(session, img) for img in images_svc.list_images(session)]


@mcp.tool()
def image_vulnerabilities(ref: str) -> ImageDetailOut:
    """Full vulnerability list for an image's latest scan. Raises if not found."""
    with get_session() as session:
        image = images_svc.get_image(session, ref)
        if image is None:
            raise ValueError(f"Image '{ref}' not found")
        scan = images_svc.latest_scan(session, image)
        return ImageDetailOut(
            ref=image.ref,
            expected_noisy=image.expected_noisy,
            scanned_at=scan.scanned_at if scan else None,
            trivy_version=scan.trivy_version if scan else None,
            vulnerabilities=[v for v in (scan.vulnerabilities if scan else [])],
        )


@mcp.tool()
def vuln_summary() -> VulnSummaryOut:
    """Fleet vulnerability rollup (latest scan per image, excluding noisy images)."""
    with get_session() as session:
        return VulnSummaryOut(**images_svc.vuln_summary(session))


@mcp.tool()
def set_image_noisy(ref: str, noisy: bool) -> ImageSummaryOut:
    """Flag/unflag an image as expected-noisy (excluded from vuln_summary)."""
    with get_session() as session:
        image = images_svc.set_noisy(session, ref, noisy)
        session.flush()
        return _image_summary(session, image)


def serve() -> None:
    """Run pending DB migrations, then serve over stdio.

    Mirrors the web app's startup migration (cmdb.web.app.lifespan) so the
    server works against a fresh DB. Alembic logs to stderr, keeping stdout
    (the MCP JSON-RPC channel) clean.
    """
    from cmdb.db import run_migrations

    run_migrations()
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    serve()
