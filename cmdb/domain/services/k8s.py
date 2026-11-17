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


def rename_cluster(session: Session, name: str, new_name: str) -> K8sCluster:
    """Rename a cluster in place. Nodes, namespaces and workloads hang off the id,
    so they follow; collection finds the cluster again through its control-plane
    (see :func:`cluster_for_control_plane`), so the new name survives re-collection."""
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("New cluster name must not be empty")
    cluster = session.query(K8sCluster).filter_by(name=name).first()
    if not cluster:
        raise ValueError(f"Cluster '{name}' not found")
    if new_name == cluster.name:
        return cluster
    if session.query(K8sCluster).filter_by(name=new_name).first():
        raise ValueError(f"Cluster '{new_name}' already exists")
    cluster.name = new_name
    session.flush()
    return cluster


def cluster_for_control_plane(session: Session, hostname: str) -> K8sCluster | None:
    """The cluster this host is already a control-plane of, if exactly one.

    A kubeconfig context is only a *suggested* name: k3s reports ``default``
    everywhere, so collection falls back to the control-plane's hostname. Matching
    on membership instead of on that derived name is what lets a renamed cluster
    stay renamed rather than being recreated beside itself.
    """
    name_lower = hostname.lower()
    clusters = (
        session.query(K8sCluster)
        .join(K8sNode, K8sNode.cluster_id == K8sCluster.id)
        .join(Host, Host.id == K8sNode.host_id)
        .filter(
            K8sNode.role == K8sNodeRole.CONTROL_PLANE,
            (func.lower(Host.hostname) == name_lower)
            | (func.lower(Host.fqdn) == name_lower),
        )
        .distinct()
        .limit(2)
        .all()
    )
    return clusters[0] if len(clusters) == 1 else None


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
            f"Host '{hostname}' not found "
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
