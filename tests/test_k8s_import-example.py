import json
import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import ImportSource, K8sCluster, K8sNamespace, K8sNode, K8sNodeRole
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.k8s_import import (
    import_cluster,
    import_from_path,
    parse_kubectl_json,
)


@pytest.fixture
def populated_db(db: Session, host_facts: dict) -> Session:
    import_host(db, host_facts)
    db.commit()
    return db


@pytest.fixture
def cluster_data() -> dict:
    return {
        "cluster": "test-cluster",
        "description": "Test k3s cluster",
        "nodes": [{"hostname": "testhost", "role": "control-plane"}],
        "namespaces": ["default", "kube-system"],
    }


# --- import_cluster ---

def test_import_cluster_creates_cluster(populated_db, cluster_data):
    import_cluster(populated_db, cluster_data)
    clusters = populated_db.query(K8sCluster).all()
    assert len(clusters) == 1
    assert clusters[0].name == "test-cluster"
    assert clusters[0].description == "Test k3s cluster"


def test_import_cluster_upserts_description(populated_db, cluster_data):
    import_cluster(populated_db, cluster_data)
    cluster_data["description"] = "Updated description"
    import_cluster(populated_db, cluster_data)
    clusters = populated_db.query(K8sCluster).all()
    assert len(clusters) == 1
    assert clusters[0].description == "Updated description"


def test_import_cluster_creates_node(populated_db, cluster_data):
    import_cluster(populated_db, cluster_data)
    nodes = populated_db.query(K8sNode).all()
    assert len(nodes) == 1
    assert nodes[0].role == K8sNodeRole.CONTROL_PLANE


def test_import_cluster_node_role_worker(populated_db):
    data = {"cluster": "test-cluster", "nodes": [{"hostname": "testhost", "role": "worker"}]}
    import_cluster(populated_db, data)
    node = populated_db.query(K8sNode).first()
    assert node.role == K8sNodeRole.WORKER


def test_import_cluster_node_role_master_alias(populated_db):
    data = {"cluster": "test-cluster", "nodes": [{"hostname": "testhost", "role": "master"}]}
    import_cluster(populated_db, data)
    node = populated_db.query(K8sNode).first()
    assert node.role == K8sNodeRole.CONTROL_PLANE


def test_import_cluster_unknown_node_is_non_fatal(populated_db):
    data = {
        "cluster": "test-cluster",
        "nodes": [{"hostname": "ghost-node", "role": "worker"}],
    }
    counts = import_cluster(populated_db, data)
    assert counts["nodes"] == 0
    assert counts["nodes_failed"] == 1
    assert any("ghost-node" in e for e in counts["errors"])
    assert populated_db.query(K8sCluster).count() == 1


def test_import_cluster_mixed_nodes(populated_db):
    data = {
        "cluster": "test-cluster",
        "nodes": [
            {"hostname": "testhost", "role": "control-plane"},
            {"hostname": "ghost-node", "role": "worker"},
        ],
    }
    counts = import_cluster(populated_db, data)
    assert counts["nodes"] == 1
    assert counts["nodes_failed"] == 1


def test_import_cluster_creates_namespaces(populated_db, cluster_data):
    import_cluster(populated_db, cluster_data)
    ns = populated_db.query(K8sNamespace).all()
    assert len(ns) == 2
    assert {n.name for n in ns} == {"default", "kube-system"}


def test_import_cluster_namespace_object_form(populated_db):
    data = {
        "cluster": "test-cluster",
        "namespaces": [{"name": "default"}, {"name": "monitoring"}],
    }
    import_cluster(populated_db, data)
    ns_names = {n.name for n in populated_db.query(K8sNamespace).all()}
    assert ns_names == {"default", "monitoring"}


def test_import_cluster_missing_cluster_key_raises(populated_db):
    with pytest.raises(ValueError, match="'cluster'"):
        import_cluster(populated_db, {"nodes": []})


def test_import_cluster_returns_counts(populated_db, cluster_data):
    counts = import_cluster(populated_db, cluster_data)
    assert counts["clusters"] == 1
    assert counts["nodes"] == 1
    assert counts["namespaces"] == 2
    assert counts["nodes_failed"] == 0
    assert counts["errors"] == []


# --- parse_kubectl_json ---

def test_parse_kubectl_json_detects_roles():
    nodes = json.dumps({"items": [
        {"metadata": {"name": "cp", "labels": {"node-role.kubernetes.io/control-plane": ""}}},
        {"metadata": {"name": "w1", "labels": {}}},
        {"metadata": {"name": "e1", "labels": {"node-role.kubernetes.io/etcd": ""}}},
        {"metadata": {"name": "m1", "labels": {"node-role.kubernetes.io/master": ""}}},
    ]})
    ns = json.dumps({"items": [
        {"metadata": {"name": "default"}},
        {"metadata": {"name": "kube-system"}},
    ]})

    data = parse_kubectl_json(nodes, ns, "mycluster", "desc")

    assert data["cluster"] == "mycluster"
    assert data["description"] == "desc"
    roles = {n["hostname"]: n["role"] for n in data["nodes"]}
    assert roles == {"cp": "control-plane", "w1": "worker", "e1": "etcd", "m1": "control-plane"}
    assert data["namespaces"] == ["default", "kube-system"]


def test_parse_kubectl_json_feeds_import_cluster(populated_db):
    nodes = json.dumps({"items": [
        {"metadata": {"name": "testhost", "labels": {"node-role.kubernetes.io/control-plane": ""}}},
    ]})
    ns = json.dumps({"items": [{"metadata": {"name": "default"}}]})

    data = parse_kubectl_json(nodes, ns, "live-cluster")
    counts = import_cluster(populated_db, data)

    assert counts["clusters"] == 1
    assert counts["nodes"] == 1
    node = populated_db.query(K8sNode).first()
    assert node.role == K8sNodeRole.CONTROL_PLANE


def test_parse_kubectl_json_handles_empty_items():
    data = parse_kubectl_json('{"items": []}', '{"items": []}', "empty")
    assert data["nodes"] == []
    assert data["namespaces"] == []


# --- import_from_path ---

def test_import_from_path_single_file(populated_db, tmp_path, cluster_data):
    f = tmp_path / "cluster.json"
    f.write_text(json.dumps(cluster_data))
    log = import_from_path(populated_db, str(f), ImportSource.CLI)
    assert log.k8s_clusters_upserted == 1
    assert log.k8s_nodes_upserted == 1
    assert log.k8s_namespaces_upserted == 2
    assert log.source == ImportSource.CLI
    assert log.hosts_upserted == 0


def test_import_from_path_array_of_clusters(populated_db, tmp_path):
    data = [
        {"cluster": "cluster-a", "nodes": [], "namespaces": []},
        {"cluster": "cluster-b", "nodes": [], "namespaces": []},
    ]
    f = tmp_path / "clusters.json"
    f.write_text(json.dumps(data))
    log = import_from_path(populated_db, str(f), ImportSource.CLI)
    assert log.k8s_clusters_upserted == 2


def test_import_from_path_directory(populated_db, tmp_path, cluster_data):
    (tmp_path / "cluster-a.json").write_text(json.dumps(cluster_data))
    second = dict(cluster_data, cluster="cluster-b")
    (tmp_path / "cluster-b.json").write_text(json.dumps(second))
    log = import_from_path(populated_db, str(tmp_path), ImportSource.CLI)
    assert log.k8s_clusters_upserted == 2


def test_import_from_path_idempotent(populated_db, tmp_path, cluster_data):
    f = tmp_path / "cluster.json"
    f.write_text(json.dumps(cluster_data))
    import_from_path(populated_db, str(f), ImportSource.CLI)
    import_from_path(populated_db, str(f), ImportSource.CLI)
    assert populated_db.query(K8sCluster).count() == 1
    assert populated_db.query(K8sNamespace).count() == 2


def test_import_from_path_bad_json_non_fatal(populated_db, tmp_path, cluster_data):
    (tmp_path / "good.json").write_text(json.dumps(cluster_data))
    (tmp_path / "bad.json").write_text("not json {{{")
    log = import_from_path(populated_db, str(tmp_path), ImportSource.CLI)
    assert log.k8s_clusters_upserted == 1
    assert log.notes is not None
    assert "bad.json" in log.notes


def test_import_from_path_node_warnings_in_notes(populated_db, tmp_path):
    data = {
        "cluster": "test-cluster",
        "nodes": [
            {"hostname": "testhost", "role": "control-plane"},
            {"hostname": "no-such-host", "role": "worker"},
        ],
    }
    f = tmp_path / "cluster.json"
    f.write_text(json.dumps(data))
    log = import_from_path(populated_db, str(f), ImportSource.CLI)
    assert log.k8s_nodes_upserted == 1
    assert log.notes is not None
    assert "no-such-host" in log.notes


def test_import_cluster_node_matches_by_fqdn(db: Session, host_facts: dict):
    # Host stored with short hostname but FQDN matching the K8s node name
    host_facts["ansible_facts"]["ansible_hostname"] = "test-node-1"
    host_facts["ansible_facts"]["ansible_fqdn"] = "test-node-1"
    import_host(db, host_facts)
    db.commit()

    data = {
        "cluster": "test-cluster",
        "nodes": [{"hostname": "test-node-1", "role": "control-plane"}],
        "namespaces": [],
    }
    counts = import_cluster(db, data)
    assert counts["nodes"] == 1
    assert counts["nodes_failed"] == 0


def test_import_cluster_node_matches_case_insensitive(db: Session, host_facts: dict):
    # Ansible stores 'Test-Node-1', kubectl exports 'test-node-1'
    host_facts["ansible_facts"]["ansible_hostname"] = "Test-Node-1"
    host_facts["ansible_facts"]["ansible_fqdn"] = "Test-Node-1"
    import_host(db, host_facts)
    db.commit()

    data = {
        "cluster": "test-cluster",
        "nodes": [{"hostname": "test-node-1", "role": "control-plane"}],
        "namespaces": [],
    }
    counts = import_cluster(db, data)
    assert counts["nodes"] == 1
    assert counts["nodes_failed"] == 0
