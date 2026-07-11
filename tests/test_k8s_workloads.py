import json

import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import K8sCluster, K8sWorkload
from cmdb.domain.services.k8s import add_cluster
from cmdb.domain.services.k8s_import import (
    import_cluster,
    import_workloads,
    parse_pods_json,
)


def _pods_doc(items=None):
    if items is None:
        items = [
            {
                "metadata": {"name": "web-1", "namespace": "default"},
                "status": {"phase": "Running"},
                "spec": {
                    "containers": [
                        {"name": "app", "image": "nginx:1.25"},
                        {"name": "sidecar", "image": "library/busybox"},
                    ]
                },
            },
            {
                "metadata": {"name": "job-1", "namespace": "batch"},
                "status": {"phase": "Succeeded"},
                "spec": {"containers": [{"name": "j", "image": "alpine:3"}]},
            },
            {
                "metadata": {"name": "pinned-1", "namespace": "default"},
                "status": {"phase": "Running"},
                "spec": {
                    "containers": [
                        {"name": "p", "image": "registry.example.lan/app@sha256:abc"}
                    ]
                },
            },
        ]
    return json.dumps({"items": items})


# --- parse_pods_json ---


def test_parse_pods_extracts_running_containers():
    rows = parse_pods_json(_pods_doc())
    assert {
        "namespace": "default",
        "pod_name": "web-1",
        "container_name": "app",
        "image": "nginx:1.25",
    } in rows
    # multi-container pods produce one row per container
    assert sum(1 for r in rows if r["pod_name"] == "web-1") == 2


def test_parse_pods_skips_non_running():
    rows = parse_pods_json(_pods_doc())
    assert not any(r["pod_name"] == "job-1" for r in rows)


def test_parse_pods_skips_nameless_items():
    rows = parse_pods_json(
        _pods_doc(
            [
                {
                    "metadata": {"namespace": "default"},  # no pod name
                    "status": {"phase": "Running"},
                    "spec": {"containers": [{"name": "x", "image": "a:1"}]},
                }
            ]
        )
    )
    assert rows == []


# --- import_workloads ---


def test_import_workloads_inserts_with_canonical_join_key(db: Session):
    add_cluster(db, "demo-cluster")
    n = import_workloads(db, "demo-cluster", parse_pods_json(_pods_doc()))
    assert n == 3
    by_container = {w.container_name: w for w in db.query(K8sWorkload).all()}
    assert by_container["app"].image_canonical == "nginx:1.25"
    # tagless + library/ prefix canonicalize like Container joins do
    assert by_container["sidecar"].image_canonical == "busybox:latest"
    # digest-only refs keep the row but get no canonical join key: coercing
    # repo@sha256 to :latest would fabricate a match against another build
    assert by_container["p"].image_canonical is None
    assert by_container["p"].image == "registry.example.lan/app@sha256:abc"


def test_import_workloads_replaces_per_cluster(db: Session):
    add_cluster(db, "demo-cluster")
    add_cluster(db, "other-cluster")
    import_workloads(db, "demo-cluster", parse_pods_json(_pods_doc()))
    import_workloads(
        db,
        "other-cluster",
        [
            {
                "namespace": "kube-system",
                "pod_name": "dns-1",
                "container_name": "coredns",
                "image": "coredns/coredns:1.11",
            }
        ],
    )

    # re-import demo-cluster with one pod gone -> its rows are replaced
    import_workloads(
        db,
        "demo-cluster",
        [
            {
                "namespace": "default",
                "pod_name": "web-1",
                "container_name": "app",
                "image": "nginx:1.25",
            }
        ],
    )

    demo = db.query(K8sCluster).filter_by(name="demo-cluster").one()
    other = db.query(K8sCluster).filter_by(name="other-cluster").one()
    assert db.query(K8sWorkload).filter_by(cluster_id=demo.id).count() == 1
    # other clusters untouched
    assert db.query(K8sWorkload).filter_by(cluster_id=other.id).count() == 1


def test_import_workloads_unknown_cluster_raises(db: Session):
    with pytest.raises(ValueError, match="not found"):
        import_workloads(db, "ghost-cluster", [])


def test_cluster_delete_cascades_workloads(db: Session):
    add_cluster(db, "demo-cluster")
    import_workloads(db, "demo-cluster", parse_pods_json(_pods_doc()))
    cluster = db.query(K8sCluster).filter_by(name="demo-cluster").one()
    db.delete(cluster)
    db.flush()
    assert db.query(K8sWorkload).count() == 0


# --- import_cluster file contract ---


def test_import_cluster_accepts_workloads_key(db: Session):
    counts = import_cluster(
        db,
        {
            "cluster": "demo-cluster",
            "nodes": [],
            "namespaces": ["default"],
            "workloads": [
                {
                    "namespace": "default",
                    "pod_name": "web-1",
                    "container_name": "app",
                    "image": "nginx:1.25",
                }
            ],
        },
    )
    assert counts["workloads"] == 1
    assert db.query(K8sWorkload).count() == 1


def test_import_cluster_without_workloads_key_leaves_rows(db: Session):
    import_cluster(
        db,
        {
            "cluster": "demo-cluster",
            "nodes": [],
            "namespaces": [],
            "workloads": [
                {
                    "namespace": "default",
                    "pod_name": "web-1",
                    "container_name": "app",
                    "image": "nginx:1.25",
                }
            ],
        },
    )
    # a later import with no workloads key (e.g. an older export file) must
    # not wipe what a previous collection stored
    import_cluster(db, {"cluster": "demo-cluster", "nodes": [], "namespaces": []})
    assert db.query(K8sWorkload).count() == 1
