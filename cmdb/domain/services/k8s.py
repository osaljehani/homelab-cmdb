from sqlalchemy import func
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, K8sCluster, K8sNode, K8sNodeRole


def add_cluster(session: Session, name: str, description: str | None = None) -> K8sCluster:
    cluster = K8sCluster(name=name, description=description)
    session.add(cluster)
    session.flush()
    return cluster


def list_clusters(session: Session) -> list[K8sCluster]:
    return session.query(K8sCluster).order_by(K8sCluster.name).all()


def delete_cluster(session: Session, name: str) -> bool:
    cluster = session.query(K8sCluster).filter_by(name=name).first()
    if not cluster:
        return False
    session.delete(cluster)
    return True


def add_node(session: Session, hostname: str, cluster_name: str, role: K8sNodeRole) -> K8sNode:
    name_lower = hostname.lower()
    host = (
        session.query(Host)
        .filter(
            (func.lower(Host.hostname) == name_lower)
            | (func.lower(Host.fqdn) == name_lower)
        )
        .first()
    )
    if not host:
        raise ValueError(
            f"Host '{hostname}' not found — "
            "ensure the host is imported via Ansible and its hostname or FQDN matches the K8s node name"
        )
    cluster = session.query(K8sCluster).filter_by(name=cluster_name).first()
    if not cluster:
        raise ValueError(f"Cluster '{cluster_name}' not found")
    existing = session.query(K8sNode).filter_by(host_id=host.id, cluster_id=cluster.id).first()
    if existing:
        existing.role = role
        return existing
    node = K8sNode(host_id=host.id, cluster_id=cluster.id, role=role)
    session.add(node)
    session.flush()
    return node


def list_nodes(session: Session, cluster_name: str) -> list[K8sNode]:
    cluster = session.query(K8sCluster).filter_by(name=cluster_name).first()
    if not cluster:
        raise ValueError(f"Cluster '{cluster_name}' not found")
    return list(cluster.nodes)


def remove_node(session: Session, hostname: str, cluster_name: str) -> bool:
    host = session.query(Host).filter_by(hostname=hostname).first()
    cluster = session.query(K8sCluster).filter_by(name=cluster_name).first()
    if not host or not cluster:
        return False
    node = session.query(K8sNode).filter_by(host_id=host.id, cluster_id=cluster.id).first()
    if not node:
        return False
    session.delete(node)
    return True
