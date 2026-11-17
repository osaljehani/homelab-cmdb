import pytest
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.k8s import (
    add_cluster, list_clusters, delete_cluster,
    add_node, list_nodes, remove_node
)
from cmdb.domain.models import K8sNodeRole


@pytest.fixture
def populated_db(db, host_facts):
    import_host(db, host_facts)
    return db


def test_add_cluster(populated_db):
    cluster = add_cluster(populated_db, "homelab-k3s", description="k3s cluster")
    assert cluster.id is not None
    assert cluster.name == "homelab-k3s"


def test_add_cluster_duplicate_raises(populated_db):
    add_cluster(populated_db, "homelab-k3s")
    with pytest.raises(Exception):
        add_cluster(populated_db, "homelab-k3s")
        populated_db.flush()


def test_list_clusters(populated_db):
    add_cluster(populated_db, "cluster-a")
    add_cluster(populated_db, "cluster-b")
    clusters = list_clusters(populated_db)
    assert len(clusters) == 2
    assert clusters[0].name == "cluster-a"


def test_delete_cluster(populated_db):
    add_cluster(populated_db, "to-delete")
    assert delete_cluster(populated_db, "to-delete") is True
    assert len(list_clusters(populated_db)) == 0


def test_delete_cluster_not_found(populated_db):
    assert delete_cluster(populated_db, "ghost") is False


def test_add_node(populated_db):
    add_cluster(populated_db, "k3s")
    node = add_node(populated_db, "testhost", "k3s", K8sNodeRole.CONTROL_PLANE)
    assert node.id is not None
    assert node.role == K8sNodeRole.CONTROL_PLANE


def test_add_node_updates_role(populated_db):
    add_cluster(populated_db, "k3s")
    add_node(populated_db, "testhost", "k3s", K8sNodeRole.WORKER)
    node = add_node(populated_db, "testhost", "k3s", K8sNodeRole.CONTROL_PLANE)
    assert node.role == K8sNodeRole.CONTROL_PLANE


def test_add_node_unknown_host_raises(populated_db):
    add_cluster(populated_db, "k3s")
    with pytest.raises(ValueError, match="Host"):
        add_node(populated_db, "ghost", "k3s", K8sNodeRole.WORKER)


def test_add_node_unknown_cluster_raises(populated_db):
    with pytest.raises(ValueError, match="Cluster"):
        add_node(populated_db, "testhost", "ghost", K8sNodeRole.WORKER)


def test_list_nodes(populated_db):
    add_cluster(populated_db, "k3s")
    add_node(populated_db, "testhost", "k3s", K8sNodeRole.CONTROL_PLANE)
    nodes = list_nodes(populated_db, "k3s")
    assert len(nodes) == 1
    assert nodes[0].host.hostname == "testhost"


def test_remove_node(populated_db):
    add_cluster(populated_db, "k3s")
    add_node(populated_db, "testhost", "k3s", K8sNodeRole.WORKER)
    assert remove_node(populated_db, "testhost", "k3s") is True
    assert len(list_nodes(populated_db, "k3s")) == 0


def test_remove_node_not_found(populated_db):
    add_cluster(populated_db, "k3s")
    assert remove_node(populated_db, "testhost", "k3s") is False


# --- rename_cluster ----------------------------------------------------------


def test_rename_cluster_keeps_nodes(populated_db):
    from cmdb.domain.services.k8s import rename_cluster

    add_cluster(populated_db, "testhost")
    add_node(populated_db, "testhost", "testhost", K8sNodeRole.CONTROL_PLANE)

    cluster = rename_cluster(populated_db, "testhost", "homelab-k3s")

    assert cluster.name == "homelab-k3s"
    assert [c.name for c in list_clusters(populated_db)] == ["homelab-k3s"]
    assert len(list_nodes(populated_db, "homelab-k3s")) == 1


def test_rename_cluster_missing_raises(populated_db):
    from cmdb.domain.services.k8s import rename_cluster

    with pytest.raises(ValueError, match="not found"):
        rename_cluster(populated_db, "nope", "anything")


def test_rename_cluster_onto_existing_name_raises(populated_db):
    from cmdb.domain.services.k8s import rename_cluster

    add_cluster(populated_db, "cluster-a")
    add_cluster(populated_db, "cluster-b")
    with pytest.raises(ValueError, match="already exists"):
        rename_cluster(populated_db, "cluster-a", "cluster-b")


def test_rename_cluster_blank_name_raises(populated_db):
    from cmdb.domain.services.k8s import rename_cluster

    add_cluster(populated_db, "cluster-a")
    with pytest.raises(ValueError, match="empty"):
        rename_cluster(populated_db, "cluster-a", "   ")


def test_cli_cluster_rename(db, monkeypatch):
    from contextlib import contextmanager

    from typer.testing import CliRunner

    from cmdb.cli.main import app

    @contextmanager
    def _fake_session():
        yield db

    monkeypatch.setattr("cmdb.cli.k8s.get_session", _fake_session)
    add_cluster(db, "testhost")
    runner = CliRunner()

    r = runner.invoke(app, ["k8s", "cluster", "rename", "testhost", "homelab-k3s"])
    assert r.exit_code == 0, r.output
    assert [c.name for c in list_clusters(db)] == ["homelab-k3s"]

    r = runner.invoke(app, ["k8s", "cluster", "rename", "ghost", "x"])
    assert r.exit_code != 0
    assert "not found" in r.output
