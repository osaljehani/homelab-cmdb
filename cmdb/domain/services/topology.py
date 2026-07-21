"""Assemble the infrastructure graph for the topology visualizer.

Output is Cytoscape.js "elements" JSON: {nodes, edges, meta}. Layer
membership is encoded as classes (layer-infra / layer-network /
layer-exposure / layer-images / layer-k8s) so the client can toggle layers
without re-fetching.

Compound nesting (Cytoscape `parent`) now carries the whole readable
hierarchy so the client can collapse/expand it:

    host    -> compose project -> container
    cluster -> namespace       -> workload

The cluster is therefore a compound container (not a lone hexagon linked by
edges): collapsing it folds an entire k8s cluster down to a single node,
which is what keeps the Kubernetes layer legible at 60+ workloads.

Images move OFF the canvas by default: instead of an image node + edge per
container/workload, every leaf carries a worst-severity class (sev-*) that
the client draws as a colored ring, plus a `vulns` count object for the
detail panel. Group nodes (namespace / compose / cluster) get `has_crit`
when anything they contain is critical, so a *collapsed* group still shows
the red ring. The image nodes themselves are still emitted on the
`layer-images` layer (off by default) for anyone who wants the old view.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from cmdb.domain.models import Container, Host, Image, K8sCluster, K8sWorkload
from cmdb.domain.refs import canonical_ref
from cmdb.domain.services.images import latest_scan
from cmdb.domain.services.network import subnet_of as _subnet_of


def _severity_class(session: Session, image: Image | None) -> str:
    if image is None:
        return "sev-unscanned"
    if image.expected_noisy:
        return "sev-noisy"
    scan = latest_scan(session, image)
    if scan is None:
        return "sev-unscanned"
    if scan.critical:
        return "sev-critical"
    if scan.high:
        return "sev-high"
    if scan.medium:
        return "sev-medium"
    return "sev-clean"


def _severity_meta(session: Session, image: Image | None) -> tuple[str, dict | None]:
    """Return (sev_class, vuln_counts). vuln_counts is None when unscanned."""
    sev = _severity_class(session, image)
    if image is None:
        return sev, None
    scan = latest_scan(session, image)
    if scan is None:
        return sev, None
    return sev, {
        "critical": scan.critical,
        "high": scan.high,
        "medium": scan.medium,
        "low": scan.low,
    }


def build_topology(session: Session) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    # id -> node dict, so we can append classes (has_crit) after the fact and
    # tally child counts onto the group labels once everything is built.
    node_index: dict[str, dict] = {}
    child_count: dict[str, int] = {}
    crit_groups: set[str] = set()

    def add_node(data: dict, classes: str) -> dict:
        node = {"data": data, "classes": classes}
        nodes.append(node)
        node_index[data["id"]] = node
        parent = data.get("parent")
        if parent:
            child_count[parent] = child_count.get(parent, 0) + 1
        return node

    def mark_crit(node_id: str | None) -> None:
        # Walk a leaf's ancestry, flagging every enclosing group as critical.
        while node_id:
            crit_groups.add(node_id)
            parent = node_index.get(node_id, {}).get("data", {}).get("parent")
            node_id = parent

    hosts = session.query(Host).order_by(Host.hostname).all()
    clusters = session.query(K8sCluster).order_by(K8sCluster.name).all()
    containers = session.query(Container).order_by(Container.name).all()
    images_by_ref = {img.ref: img for img in session.query(Image).all()}

    subnets: set[str] = set()
    any_tailscale = any(h.tailscale_ipv4 for h in hosts)

    for host in hosts:
        listeners = [
            {
                "proto": p.proto,
                "address": p.address,
                "port": p.port,
                "process": p.process,
            }
            for p in sorted(host.listening_ports, key=lambda p: p.port or 0)
        ]
        exposed = [p for p in listeners if p["address"] not in ("127.0.0.1", "::1")]
        services = [
            {"proto": s.proto, "port": s.port, "target": s.target, "funnel": s.funnel}
            for s in sorted(host.tailscale_services, key=lambda s: s.port or 0)
        ]
        funnel = any(s["funnel"] for s in services)

        classes = ["host", "layer-infra"]
        if exposed or funnel:
            classes.append("exposed")
        if funnel:
            classes.append("funnel")
        if host.tailscale_exit_node:
            classes.append("exit-node")

        add_node(
            {
                "id": f"host:{host.hostname}",
                "label": host.hostname,
                "kind": "host",
                "url": f"/hosts/{host.hostname}",
                "ip": host.primary_ipv4,
                "os": host.os_distribution,
                "online": host.tailscale_online,
                "exposed_ports": len(exposed),
                "funnel": funnel,
                "ports": listeners,
                "services": services,
            },
            " ".join(classes),
        )

        subnet = _subnet_of(host.primary_ipv4)
        if subnet:
            subnets.add(subnet)
            edges.append(
                {
                    "data": {
                        "id": f"lan:{host.hostname}",
                        "source": f"host:{host.hostname}",
                        "target": f"subnet:{subnet}",
                        "kind": "lan",
                    },
                    "classes": "layer-network lan",
                }
            )

        if host.tailscale_ipv4:
            ts_class = "ts-online" if host.tailscale_online else "ts-offline"
            edges.append(
                {
                    "data": {
                        "id": f"ts:{host.hostname}",
                        "source": f"host:{host.hostname}",
                        "target": "tailnet",
                        "kind": "tailscale",
                        "exit_node": bool(host.tailscale_exit_node),
                    },
                    "classes": f"layer-network tailscale {ts_class}",
                }
            )

    for subnet in sorted(subnets):
        add_node(
            {"id": f"subnet:{subnet}", "label": subnet, "kind": "subnet"},
            "subnet layer-network",
        )

    if any_tailscale:
        add_node(
            {"id": "tailnet", "label": "tailnet", "kind": "tailnet"},
            "tailnet layer-network",
        )

    # Cluster is now a compound container. Its namespaces are `parent`ed to it
    # (below) rather than joined by a k8s-ns edge, so expand/collapse folds the
    # whole cluster to one node.
    for cluster in clusters:
        add_node(
            {
                "id": f"cluster:{cluster.name}",
                "label": cluster.name,
                "kind": "k8s_cluster",
                "url": "/k8s",
            },
            "cluster k8s layer-infra",
        )
        for member in cluster.nodes:
            edges.append(
                {
                    "data": {
                        "id": f"k8s:{member.host.hostname}:{cluster.name}",
                        "source": f"host:{member.host.hostname}",
                        "target": f"cluster:{cluster.name}",
                        "kind": "k8s_member",
                        "role": member.role.value,
                    },
                    "classes": "layer-infra k8s-member",
                }
            )

    compose_seen: set[str] = set()
    image_refs_used: set[str] = set()
    for c in containers:
        hostname = c.host.hostname
        parent = f"host:{hostname}"
        if c.compose_project:
            compose_id = f"compose:{hostname}/{c.compose_project}"
            if compose_id not in compose_seen:
                compose_seen.add(compose_id)
                add_node(
                    {
                        "id": compose_id,
                        "label": c.compose_project,
                        "kind": "compose",
                        "parent": f"host:{hostname}",
                    },
                    "compose layer-infra",
                )
            parent = compose_id

        state = (c.state or "unknown").lower()
        sev, vulns = "sev-unscanned", None
        cref = None
        if c.image:
            cref = canonical_ref(c.image)
            sev, vulns = _severity_meta(session, images_by_ref.get(cref))

        container_id = f"container:{hostname}/{c.name}"
        add_node(
            {
                "id": container_id,
                "label": c.name,
                "kind": "container",
                "parent": parent,
                "state": c.state,
                "image": c.image,
                "vulns": vulns,
                "url": f"/hosts/{hostname}",
            },
            f"container layer-infra state-{state} {sev}",
        )
        if sev == "sev-critical":
            mark_crit(container_id)

        # Image node still emitted on layer-images (off by default) for the
        # optional legacy view; the ring above is the primary signal.
        if cref:
            if cref not in image_refs_used:
                image_refs_used.add(cref)
                image = images_by_ref.get(cref)
                add_node(
                    {
                        "id": f"image:{cref}",
                        "label": cref,
                        "kind": "image",
                        "url": f"/images/{cref}" if image else None,
                    },
                    f"image layer-images {sev}",
                )
            edges.append(
                {
                    "data": {
                        "id": f"runs:{hostname}/{c.name}",
                        "source": container_id,
                        "target": f"image:{cref}",
                        "kind": "runs",
                    },
                    "classes": f"layer-images runs {sev}",
                }
            )

    # Kubernetes layer: namespace compounds (per cluster) holding one node per
    # distinct workload, replicas collapsed. Namespaces are parented to their
    # cluster so the cluster collapses cleanly.
    cluster_names = {c.id: c.name for c in clusters}
    grouped: dict[tuple, dict] = {}
    for w in session.query(K8sWorkload).all():
        key = (w.cluster_id, w.namespace, w.container_name, w.image)
        g = grouped.setdefault(key, {"replicas": 0, "canonical": w.image_canonical})
        g["replicas"] += 1

    ns_seen: set[str] = set()
    for (cluster_id, namespace, container_name, raw_image), g in sorted(
        grouped.items(), key=lambda kv: kv[0]
    ):
        cluster_name = cluster_names.get(cluster_id)
        if cluster_name is None:
            continue
        ns_id = f"ns:{cluster_name}/{namespace}"
        if ns_id not in ns_seen:
            ns_seen.add(ns_id)
            add_node(
                {
                    "id": ns_id,
                    "label": namespace,
                    "kind": "k8s_namespace",
                    "cluster": cluster_name,
                    "parent": f"cluster:{cluster_name}",
                    "url": "/k8s",
                },
                "k8s-namespace layer-k8s",
            )

        replicas = g["replicas"]
        label = container_name if replicas == 1 else f"{container_name} ×{replicas}"
        wl_id = f"workload:{cluster_name}/{namespace}/{container_name}@{raw_image}"

        cref = g["canonical"]
        sev, vulns = "sev-unscanned", None
        if cref:
            sev, vulns = _severity_meta(session, images_by_ref.get(cref))

        add_node(
            {
                "id": wl_id,
                "label": label,
                "kind": "k8s_workload",
                "parent": ns_id,
                "cluster": cluster_name,
                "namespace": namespace,
                "image": raw_image,
                "replicas": replicas,
                "vulns": vulns,
                "url": "/k8s",
            },
            f"k8s-workload layer-k8s {sev}",
        )
        if sev == "sev-critical":
            mark_crit(wl_id)

        if not cref:
            # digest-only ref: no fabricated image match; raw ref shows in panel.
            continue
        if cref not in image_refs_used:
            image_refs_used.add(cref)
            image = images_by_ref.get(cref)
            add_node(
                {
                    "id": f"image:{cref}",
                    "label": cref,
                    "kind": "image",
                    "url": f"/images/{cref}" if image else None,
                },
                f"image layer-images layer-k8s {sev}",
            )
        edges.append(
            {
                "data": {
                    "id": f"k8s-runs:{cluster_name}/{namespace}/{container_name}@{raw_image}",
                    "source": wl_id,
                    "target": f"image:{cref}",
                    "kind": "runs",
                },
                "classes": f"layer-images layer-k8s runs {sev}",
            }
        )

    # Post-pass: fold child counts into group labels ("falco ·5") and flag any
    # group that (transitively) contains a critical so a collapsed node shows
    # the red ring.
    for node_id, node in node_index.items():
        count = child_count.get(node_id)
        if count and node["data"].get("kind") in (
            "k8s_cluster",
            "k8s_namespace",
            "compose",
        ):
            node["data"]["count"] = count
            node["data"]["label"] = f"{node['data']['label']} ·{count}"
        if node_id in crit_groups and node["data"].get("kind") in (
            "k8s_cluster",
            "k8s_namespace",
            "compose",
        ):
            node["classes"] += " has-crit"

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "hosts": len(hosts),
            "containers": len(containers),
            "clusters": len(clusters),
            "workloads": len(grouped),
            "namespaces": len(ns_seen),
            "generated_at": datetime.utcnow().isoformat(),
        },
    }
