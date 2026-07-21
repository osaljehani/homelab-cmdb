"""Assemble the infrastructure graph for the topology visualizer.

Output is Cytoscape.js "elements" JSON: {nodes, edges, meta}. Layer
membership is encoded as classes (layer-infra / layer-network /
layer-exposure / layer-images / layer-k8s) so the client can toggle layers
without re-fetching. Compound nesting (Cytoscape `parent`) is used for
host -> compose project -> container and for k8s namespace -> workload;
every other relation is an edge (the cluster hexagon stays a plain node so
it keeps its shape, linked to its namespaces by edges).
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


def build_topology(session: Session) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []

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

        nodes.append(
            {
                "data": {
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
                "classes": " ".join(classes),
            }
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
        nodes.append(
            {
                "data": {"id": f"subnet:{subnet}", "label": subnet, "kind": "subnet"},
                "classes": "subnet layer-network",
            }
        )

    if any_tailscale:
        nodes.append(
            {
                "data": {"id": "tailnet", "label": "tailnet", "kind": "tailnet"},
                "classes": "tailnet layer-network",
            }
        )

    for cluster in clusters:
        nodes.append(
            {
                "data": {
                    "id": f"cluster:{cluster.name}",
                    "label": cluster.name,
                    "kind": "k8s_cluster",
                    "url": "/k8s",
                },
                "classes": "k8s layer-infra",
            }
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
                nodes.append(
                    {
                        "data": {
                            "id": compose_id,
                            "label": c.compose_project,
                            "kind": "compose",
                            "parent": f"host:{hostname}",
                        },
                        "classes": "compose layer-infra",
                    }
                )
            parent = compose_id

        state = (c.state or "unknown").lower()
        nodes.append(
            {
                "data": {
                    "id": f"container:{hostname}/{c.name}",
                    "label": c.name,
                    "kind": "container",
                    "parent": parent,
                    "state": c.state,
                    "image": c.image,
                    "url": f"/hosts/{hostname}",
                },
                "classes": f"container layer-infra state-{state}",
            }
        )

        if c.image:
            # Image.ref is canonical (ingest/migration), so normalize the raw
            # `docker ps` spelling before any lookup or node id.
            cref = canonical_ref(c.image)
            sev = _severity_class(session, images_by_ref.get(cref))
            if cref not in image_refs_used:
                image_refs_used.add(cref)
                image = images_by_ref.get(cref)
                nodes.append(
                    {
                        "data": {
                            "id": f"image:{cref}",
                            "label": cref,
                            "kind": "image",
                            "url": f"/images/{cref}" if image else None,
                        },
                        "classes": f"image layer-images {sev}",
                    }
                )
            edges.append(
                {
                    "data": {
                        "id": f"runs:{hostname}/{c.name}",
                        "source": f"container:{hostname}/{c.name}",
                        "target": f"image:{cref}",
                        "kind": "runs",
                    },
                    "classes": f"layer-images runs {sev}",
                }
            )

    # Kubernetes layer: namespace compounds (per cluster) holding one node per
    # distinct workload, replicas collapsed. Runs after the container loop so
    # `image_refs_used` is populated — an image first seen here is k8s-only and
    # gets a dual layer-images/layer-k8s class so it hides with either layer.
    cluster_names = {c.id: c.name for c in clusters}
    grouped: dict[tuple, dict] = {}
    for w in session.query(K8sWorkload).all():
        key = (w.cluster_id, w.namespace, w.container_name, w.image)
        g = grouped.setdefault(
            key, {"replicas": 0, "canonical": w.image_canonical}
        )
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
            nodes.append(
                {
                    "data": {
                        "id": ns_id,
                        "label": namespace,
                        "kind": "k8s_namespace",
                        "cluster": cluster_name,
                        "url": "/k8s",
                    },
                    "classes": "k8s-namespace layer-k8s",
                }
            )
            edges.append(
                {
                    "data": {
                        "id": f"k8s-ns:{cluster_name}/{namespace}",
                        "source": f"cluster:{cluster_name}",
                        "target": ns_id,
                        "kind": "k8s_ns",
                    },
                    "classes": "layer-k8s k8s-ns",
                }
            )

        replicas = g["replicas"]
        label = container_name if replicas == 1 else f"{container_name} ×{replicas}"
        wl_id = f"workload:{cluster_name}/{namespace}/{container_name}@{raw_image}"
        nodes.append(
            {
                "data": {
                    "id": wl_id,
                    "label": label,
                    "kind": "k8s_workload",
                    "parent": ns_id,
                    "cluster": cluster_name,
                    "namespace": namespace,
                    "image": raw_image,
                    "replicas": replicas,
                    "url": "/k8s",
                },
                "classes": "k8s-workload layer-k8s",
            }
        )

        cref = g["canonical"]
        if not cref:
            # digest-only ref (repo@sha256:...): no fabricated image match, so
            # no image node/edge — the raw ref shows in the detail panel.
            continue
        sev = _severity_class(session, images_by_ref.get(cref))
        if cref not in image_refs_used:
            image_refs_used.add(cref)
            image = images_by_ref.get(cref)
            nodes.append(
                {
                    "data": {
                        "id": f"image:{cref}",
                        "label": cref,
                        "kind": "image",
                        "url": f"/images/{cref}" if image else None,
                    },
                    "classes": f"image layer-images layer-k8s {sev}",
                }
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
