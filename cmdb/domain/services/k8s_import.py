import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from cmdb.domain.models import ImportLog, ImportSource, K8sCluster, K8sNamespace
from cmdb.domain.models import K8sNodeRole, K8sWorkload
from cmdb.domain.refs import canonical_ref
from cmdb.domain.services.k8s import add_cluster, add_node


def _normalise_role(raw: str) -> K8sNodeRole:
    mapping = {
        "control-plane": K8sNodeRole.CONTROL_PLANE,
        "master": K8sNodeRole.CONTROL_PLANE,
        "worker": K8sNodeRole.WORKER,
        "etcd": K8sNodeRole.ETCD,
    }
    return mapping.get(raw.lower(), K8sNodeRole.WORKER)


# Standard Kubernetes node-role labels (see scripts/k8s-export.sh for the jq twin).
_CONTROL_PLANE_LABELS = (
    "node-role.kubernetes.io/control-plane",  # k8s >= 1.20
    "node-role.kubernetes.io/master",         # legacy
)
_ETCD_LABEL = "node-role.kubernetes.io/etcd"


def _role_from_labels(labels: dict[str, Any] | None) -> str:
    """Derive a node role from its labels (mirrors the k8s-export.sh jq logic)."""
    labels = labels or {}
    if any(lbl in labels for lbl in _CONTROL_PLANE_LABELS):
        return "control-plane"
    if _ETCD_LABEL in labels:
        return "etcd"
    return "worker"


def parse_kubectl_json(
    nodes_raw: str,
    ns_raw: str,
    cluster_name: str,
    description: str | None = None,
    pods_raw: str | None = None,
) -> dict[str, Any]:
    """Turn raw ``kubectl get nodes/namespaces/pods -o json`` into an import_cluster dict.

    Done in Python rather than with jq on the remote so hosts without jq (e.g. k3s
    nodes) can still be collected. ``pods_raw`` is optional (older probes don't
    emit it); when absent the returned dict has no "workloads" key, which tells
    :func:`import_cluster` to leave existing workload rows untouched.
    """
    nodes_doc = json.loads(nodes_raw)
    ns_doc = json.loads(ns_raw)

    nodes: list[dict[str, str]] = []
    for item in nodes_doc.get("items", []):
        meta = item.get("metadata", {})
        name = meta.get("name")
        if not name:
            continue
        nodes.append({"hostname": name, "role": _role_from_labels(meta.get("labels"))})

    namespaces = [
        item.get("metadata", {}).get("name")
        for item in ns_doc.get("items", [])
        if item.get("metadata", {}).get("name")
    ]

    result = {
        "cluster": cluster_name,
        "description": description,
        "nodes": nodes,
        "namespaces": namespaces,
    }
    if pods_raw:
        result["workloads"] = parse_pods_json(pods_raw)
    return result


def parse_pods_json(pods_raw: str) -> list[dict[str, str]]:
    """Turn raw ``kubectl get pods -A -o json`` into workload rows.

    Only Running pods count — placements answer "where is this image running",
    and Succeeded/Failed pods are noise. One row per (pod, container).
    """
    doc = json.loads(pods_raw)
    rows: list[dict[str, str]] = []
    for item in doc.get("items", []):
        meta = item.get("metadata", {})
        namespace, pod_name = meta.get("namespace"), meta.get("name")
        if not namespace or not pod_name:
            continue
        if (item.get("status") or {}).get("phase") != "Running":
            continue
        for c in (item.get("spec") or {}).get("containers") or []:
            image = c.get("image")
            if not image:
                continue
            rows.append(
                {
                    "namespace": namespace,
                    "pod_name": pod_name,
                    "container_name": c.get("name") or "",
                    "image": image,
                }
            )
    return rows


def _canonical_or_none(image: str) -> str | None:
    """canonical_ref(), except digest-only refs get no join key.

    ``repo@sha256:...`` without a tag would canonicalize to ``repo:latest``,
    fabricating a match against a possibly different build — worse than no
    match. Refs with both tag and digest canonicalize fine (digest stripped).
    """
    if "@" in image:
        base = image.split("@", 1)[0]
        if ":" not in base.rsplit("/", 1)[-1]:
            return None
    return canonical_ref(image)


def _replace_workloads(
    session: Session, cluster: K8sCluster, rows: list[dict[str, Any]]
) -> int:
    """Replace the cluster's workload rows with the given snapshot."""
    session.query(K8sWorkload).filter_by(cluster_id=cluster.id).delete()
    now = datetime.utcnow()
    count = 0
    for r in rows:
        namespace, pod_name, image = (
            r.get("namespace"),
            r.get("pod_name"),
            r.get("image"),
        )
        if not namespace or not pod_name or not image:
            continue
        session.add(
            K8sWorkload(
                cluster_id=cluster.id,
                namespace=namespace,
                pod_name=pod_name,
                container_name=r.get("container_name") or "",
                image=image,
                image_canonical=_canonical_or_none(image),
                last_seen=now,
            )
        )
        count += 1
    session.flush()
    return count


def import_workloads(
    session: Session, cluster_name: str, rows: list[dict[str, Any]]
) -> int:
    """Replace a named cluster's workloads (see :func:`_replace_workloads`)."""
    cluster = session.query(K8sCluster).filter_by(name=cluster_name).first()
    if cluster is None:
        raise ValueError(f"cluster '{cluster_name}' not found")
    return _replace_workloads(session, cluster, rows)


def _normalise_namespaces(raw: list[Any]) -> list[str]:
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and "name" in item:
            result.append(item["name"])
    return result


def import_cluster(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    cluster_name = data.get("cluster")
    if not cluster_name:
        raise ValueError("'cluster' key is required")

    description = data.get("description")
    nodes_raw = data.get("nodes", [])
    namespaces_raw = data.get("namespaces", [])

    cluster = session.query(K8sCluster).filter_by(name=cluster_name).first()
    if cluster is None:
        cluster = add_cluster(session, cluster_name, description)
    elif description:
        cluster.description = description
        session.flush()

    nodes_upserted = 0
    nodes_failed = 0
    errors: list[str] = []

    for node in nodes_raw:
        hostname = node.get("hostname") or node.get("name")
        role_str = node.get("role", "worker")
        try:
            add_node(session, hostname, cluster_name, _normalise_role(role_str))
            nodes_upserted += 1
        except ValueError as exc:
            nodes_failed += 1
            errors.append(f"node '{hostname}': {exc}")

    ns_names = _normalise_namespaces(namespaces_raw)
    namespaces_upserted = 0
    for ns_name in ns_names:
        exists = (
            session.query(K8sNamespace)
            .filter_by(cluster_id=cluster.id, name=ns_name)
            .first()
        )
        if exists is None:
            session.add(K8sNamespace(cluster_id=cluster.id, name=ns_name))
            namespaces_upserted += 1

    # Replace workloads only when the record carries the key: an absent key
    # (older export files / probes) must never wipe a previous collection.
    workloads_upserted = 0
    if "workloads" in data:
        workloads_upserted = _replace_workloads(
            session, cluster, data.get("workloads") or []
        )

    session.flush()

    return {
        "clusters": 1,
        "nodes": nodes_upserted,
        "nodes_failed": nodes_failed,
        "namespaces": namespaces_upserted,
        "workloads": workloads_upserted,
        "errors": errors,
    }


def import_from_path(session: Session, path: str, source: ImportSource) -> ImportLog:
    target = Path(path)
    files = [target] if target.is_file() else [f for f in target.iterdir() if f.is_file()]

    total_clusters = 0
    total_nodes = 0
    total_namespaces = 0
    total_workloads = 0
    all_errors: list[str] = []

    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception as exc:
            all_errors.append(f"{f.name}: JSON parse error: {exc}")
            continue

        records = data if isinstance(data, list) else [data]

        for i, record in enumerate(records):
            label = f"{f.name}[{i}]" if i else f.name
            try:
                counts = import_cluster(session, record)
                total_clusters += counts["clusters"]
                total_nodes += counts["nodes"]
                total_namespaces += counts["namespaces"]
                total_workloads += counts["workloads"]
                all_errors.extend(f"{label}: {e}" for e in counts["errors"])
            except Exception as exc:
                all_errors.append(f"{label}: {exc}")

    log = ImportLog(
        source=source,
        filename=str(path),
        hosts_upserted=0,
        hosts_failed=0,
        k8s_clusters_upserted=total_clusters,
        k8s_nodes_upserted=total_nodes,
        k8s_namespaces_upserted=total_namespaces,
        k8s_workloads_upserted=total_workloads,
        notes="\n".join(all_errors) or None,
    )
    session.add(log)
    session.flush()
    return log
