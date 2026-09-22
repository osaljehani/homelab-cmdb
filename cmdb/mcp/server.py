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
from cmdb.domain.schemas import (
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
    image_summary_out,
    running_on,
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


_running_on = running_on
_image_summary = image_summary_out


@mcp.tool()
def list_image_scans() -> list[ImageSummaryOut]:
    """List scanned container images with their latest severity counts,
    per-source staleness, and running/registry-only deployment status."""
    with get_session() as session:
        return [
            _image_summary(session, row["image"], row)
            for row in images_svc.image_overview(session)
        ]


@mcp.tool()
def image_vulnerabilities(ref: str) -> ImageDetailOut:
    """Full vulnerability list for an image's latest scan. Raises if not found."""
    with get_session() as session:
        image = images_svc.get_image(session, ref)
        if image is None:
            raise ValueError(f"Image '{ref}' not found")
        row = images_svc.image_status(session, image)
        scan = row["scan"]
        return ImageDetailOut(
            ref=image.ref,
            expected_noisy=image.expected_noisy,
            scanned_at=scan.scanned_at if scan else None,
            trivy_version=scan.trivy_version if scan else None,
            stale=row["stale"],
            deployment_status=row["status"],
            running_on=_running_on(row),
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


@mcp.tool()
def delete_image(ref: str, confirm: bool = False) -> dict:
    """Delete a container image and its per-CVE scan history (scans + vulnerabilities).

    DESTRUCTIVE and irreversible. You MUST get the user's explicit confirmation
    before deleting: called with confirm=False (the default) this deletes nothing
    and returns a refusal. Only after the user confirms, call again with
    confirm=True. Use to clear a decommissioned image that no longer runs
    (the scanner will re-add it if the container comes back). Daily totals
    already captured in the dashboard vulnerability trend are kept — past trend
    points survive; today's point drops immediately. Raises if not found.
    """
    if not confirm:
        return {
            "deleted": False,
            "ref": ref,
            "message": (
                f"Refused: deleting '{ref}' removes its per-CVE scan history and "
                "cannot be undone (daily totals in the vuln trend are kept). "
                "Confirm with the user, then call again with confirm=True."
            ),
        }
    with get_session() as session:
        result = images_svc.delete_image(session, ref)
        session.flush()
        return {"deleted": True, **result}


# --- Remote (streamable-HTTP) server ---------------------------------------
#
# The remote endpoint exposes a strict READ-ONLY subset: 13 of the 23 tools.
# The other 10 -- add_tag, remove_tag, delete_host, add_cluster, delete_cluster,
# add_node, remove_node, import_ansible, set_image_noisy, delete_image -- stay
# stdio-only and are simply never registered on the remote instance. That is
# the whole mechanism: there is no runtime flag to get them wrong, and
# delete_image in particular takes an image's entire scan history with it.
#
# This works because @mcp.tool() returns the original undecorated function, so
# the names below are plain callables that can be registered a second time on a
# different FastMCP. tests/test_mcp.py relies on the same property.
#
# generate_ssh_config IS in this set and does expose the fleet's SSH layout to
# whoever holds a token. That was a deliberate call -- removing it is deleting
# one line here.
READ_ONLY_TOOLS = (
    list_hosts,
    get_host,
    host_posture,
    posture_summary,
    host_history,
    list_clusters,
    list_nodes,
    generate_inventory_yaml,
    generate_inventory_ini,
    generate_ssh_config,
    list_image_scans,
    image_vulnerabilities,
    vuln_summary,
)


def build_remote_mcp() -> "FastMCP":
    """A second FastMCP carrying only READ_ONLY_TOOLS, behind bearer auth.

    Raises if the verifier cannot be constructed. Callers must not catch that:
    a FastMCP built without a token verifier registers /mcp with **no auth
    wrapper at all** (fastmcp/server.py:1017-1024) and raises nothing, so the
    open-door state has to be unreachable rather than merely unlikely.
    """
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations
    from pydantic import AnyHttpUrl

    from cmdb.config import settings
    from cmdb.mcp.auth import AuthentikTokenVerifier

    verifier = AuthentikTokenVerifier(
        issuer=settings.mcp_issuer_url,
        audience=settings.mcp_audience,
        jwks_url=settings.mcp_jwks_url,
        userinfo_url=settings.mcp_userinfo_url,
        required_groups=settings.mcp_required_groups_set,
        leeway=settings.mcp_clock_skew_seconds,
        jwks_cache_seconds=settings.mcp_jwks_cache_seconds,
        userinfo_cache_seconds=settings.mcp_userinfo_cache_seconds,
        timeout=settings.mcp_http_timeout,
    )

    remote = FastMCP(
        "HomeLabCMDB",
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.mcp_issuer_url),
            resource_server_url=AnyHttpUrl(settings.mcp_resource_url),
            # Deliberately empty. required_scopes is the ONLY thing the SDK's
            # RequireAuthMiddleware checks, and a scope named here that no
            # Authentik mapping emits would 403 everybody. Authorization is the
            # group check in cmdb.mcp.auth instead.
            required_scopes=None,
        ),
        token_verifier=verifier,
        # Absolute path on the parent app -- see attach_remote_mcp().
        streamable_http_path="/mcp",
        # No Mcp-Session-Id, so nothing to lose across a container restart and
        # no long-lived SSE channel for the Cloudflare tunnel to time out. It
        # also avoids the session manager binding a session to the token's
        # (client_id, iss, sub), which would break when claude.ai silently
        # refreshes its access token mid-session.
        stateless_http=True,
        # One JSON body per POST instead of an SSE stream. Every tool here is a
        # plain synchronous request/response with no progress notifications, so
        # streaming buys nothing and costs two reverse-proxy hops' worth of
        # buffering risk (cloudflared, then the Authentik outpost).
        json_response=True,
        # MUST be passed explicitly. FastMCP's `host` kwarg defaults to
        # "127.0.0.1", so leaving this None auto-enables rebinding protection
        # with a LOCALHOST-ONLY allowlist -- and every request arriving with
        # Host: cmdb.oaljehani.com would be rejected. Passing a bare
        # TransportSecuritySettings() is the opposite trap: an empty allowlist
        # rejects everything.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts_list,
            allowed_origins=settings.mcp_allowed_origins_list,
        ),
    )

    for fn in READ_ONLY_TOOLS:
        remote.add_tool(fn, annotations=ToolAnnotations(readOnlyHint=True))

    if remote._token_verifier is None or remote.settings.auth is None:
        raise RuntimeError("refusing to serve MCP over HTTP without a token verifier")
    return remote


def attach_remote_mcp(app) -> object | None:
    """Register the remote MCP routes on an existing FastAPI app.

    Returns the session manager the caller's lifespan must run, or None when
    the remote endpoint is disabled.

    Mounting is the trap here, so the reasoning is worth keeping. The SDK's
    streamable_http_app() returns a Starlette whose routes are ABSOLUTE:
    "/mcp", and the RFC 9728 document at
    "/.well-known/oauth-protected-resource/mcp" -- a path the SDK builds by
    insertion (RFC 9728 3.1) from resource_server_url, not from wherever the
    app is mounted. Both must answer at the ORIGIN, because that is what the
    Authentik outpost's skip_path_regex exempts.

    Starlette's Mount defeats both. It strips its own prefix and compiles to
    "^/mcp/(?P<path>.*)$", so with streamable_http_path="/" a plain
    `POST /mcp` does not match and Starlette's redirect_slashes answers 307 to
    "/mcp/" (measured, 2026-09-22) -- while the metadata document lands at
    "/mcp/.well-known/..." where nothing looks for it. Lifting the sub-app's
    routes onto the parent instead would drop the sub-app's
    AuthenticationMiddleware, leaving RequireAuthMiddleware to 401 everything
    forever.

    So: register one exact Route per sub-app route, delegating the whole
    sub-app as a raw ASGI endpoint. Starlette treats a non-function endpoint as
    an ASGI app and calls it with the path UNMODIFIED, so the sub-app's own
    router resolves it through its full middleware stack. Paths stay absolute,
    every other path still falls through to FastAPI's own 404, and deriving
    them from sub.routes means there is no second place to keep in sync.
    """
    from starlette.routing import Route

    from cmdb.config import settings

    if not settings.mcp_remote_enabled:
        return None

    remote = build_remote_mcp()
    sub = remote.streamable_http_app()
    for index, route in enumerate(sub.routes):
        app.router.routes.append(Route(route.path, endpoint=sub, name=f"mcp_{index}"))
    return remote.session_manager


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
