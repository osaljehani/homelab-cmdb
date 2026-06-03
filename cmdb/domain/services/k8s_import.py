import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from cmdb.domain.models import ImportLog, ImportSource, K8sCluster, K8sNamespace
from cmdb.domain.models import K8sNodeRole
from cmdb.domain.services.k8s import add_cluster, add_node


def _normalise_role(raw: str) -> K8sNodeRole:
    mapping = {
        "control-plane": K8sNodeRole.CONTROL_PLANE,
        "master": K8sNodeRole.CONTROL_PLANE,
        "worker": K8sNodeRole.WORKER,
        "etcd": K8sNodeRole.ETCD,
    }
    return mapping.get(raw.lower(), K8sNodeRole.WORKER)


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

    session.flush()

    return {
        "clusters": 1,
        "nodes": nodes_upserted,
        "nodes_failed": nodes_failed,
        "namespaces": namespaces_upserted,
        "errors": errors,
    }


def import_from_path(session: Session, path: str, source: ImportSource) -> ImportLog:
    target = Path(path)
    files = [target] if target.is_file() else [f for f in target.iterdir() if f.is_file()]

    total_clusters = 0
    total_nodes = 0
    total_namespaces = 0
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
        notes="\n".join(all_errors) or None,
    )
    session.add(log)
    session.flush()
    return log
