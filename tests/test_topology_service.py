from datetime import datetime

from cmdb.domain.models import (
    Container,
    Host,
    Image,
    ImageScan,
    K8sCluster,
    K8sNode,
    K8sNodeRole,
    K8sWorkload,
    ListeningPort,
    TailscaleService,
)
from cmdb.domain.services.topology import build_topology


def _host(i, **kw):
    return Host(machine_id=f"m{i:032d}", hostname=f"host-{i}", **kw)


def _ids(graph):
    return [n["data"]["id"] for n in graph["nodes"]]


def _node(graph, node_id):
    return next(n for n in graph["nodes"] if n["data"]["id"] == node_id)


def _edge(graph, source, target):
    return next(
        e
        for e in graph["edges"]
        if e["data"]["source"] == source and e["data"]["target"] == target
    )


def test_empty_db(db):
    graph = build_topology(db)
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["meta"]["hosts"] == 0


def test_compound_chain_host_compose_container(db):
    host = _host(1)
    db.add(host)
    db.flush()
    db.add(Container(host_id=host.id, name="app", image="app:1", state="running", compose_project="stack"))
    db.add(Container(host_id=host.id, name="loose", image="tool:1", state="exited"))
    db.commit()

    graph = build_topology(db)
    app = _node(graph, "container:host-1/app")
    assert app["data"]["parent"] == "compose:host-1/stack"
    compose = _node(graph, "compose:host-1/stack")
    assert compose["data"]["parent"] == "host:host-1"
    # a container without a compose project nests directly under the host
    loose = _node(graph, "container:host-1/loose")
    assert loose["data"]["parent"] == "host:host-1"
    assert "state-exited" in loose["classes"]


def test_container_image_edge_carries_severity(db):
    host = _host(1)
    img = Image(ref="app:1", first_seen=datetime(2026, 1, 1))
    db.add_all([host, img])
    db.flush()
    db.add(Container(host_id=host.id, name="app", image="app:1", state="running"))
    db.add(ImageScan(image_id=img.id, scanned_at=datetime(2026, 6, 1), critical=3, total=3))
    db.commit()

    graph = build_topology(db)
    edge = _edge(graph, "container:host-1/app", "image:app:1")
    assert "sev-critical" in edge["classes"]
    assert "sev-critical" in _node(graph, "image:app:1")["classes"]


def test_noisy_image_severity_treated_clean(db):
    host = _host(1)
    img = Image(ref="noisy:1", first_seen=datetime(2026, 1, 1), expected_noisy=True)
    db.add_all([host, img])
    db.flush()
    db.add(Container(host_id=host.id, name="n", image="noisy:1", state="running"))
    db.add(ImageScan(image_id=img.id, scanned_at=datetime(2026, 6, 1), critical=9, total=9))
    db.commit()

    graph = build_topology(db)
    assert "sev-noisy" in _node(graph, "image:noisy:1")["classes"]


def test_unmatched_container_image_is_unscanned(db):
    host = _host(1)
    db.add(host)
    db.flush()
    db.add(Container(host_id=host.id, name="x", image="ghost:latest", state="running"))
    db.commit()

    graph = build_topology(db)
    assert "sev-unscanned" in _node(graph, "image:ghost:latest")["classes"]


def test_container_raw_ref_canonicalized_for_image_node(db):
    host = _host(1)
    img = Image(ref="nginx:1.25", first_seen=datetime(2026, 1, 1))
    db.add_all([host, img])
    db.flush()
    db.add(Container(host_id=host.id, name="web", image="library/nginx:1.25", state="running"))
    db.add(ImageScan(image_id=img.id, scanned_at=datetime(2026, 6, 1), critical=2, total=2))
    db.commit()

    graph = build_topology(db)
    node = _node(graph, "image:nginx:1.25")
    assert "sev-critical" in node["classes"]
    assert node["data"]["url"] == "/images/nginx:1.25"
    _edge(graph, "container:host-1/web", "image:nginx:1.25")
    assert "image:library/nginx:1.25" not in _ids(graph)


def test_ref_spellings_share_one_image_node(db):
    host = _host(1)
    db.add(host)
    db.flush()
    db.add(Container(host_id=host.id, name="a", image="nginx", state="running"))
    db.add(Container(host_id=host.id, name="b", image="nginx:latest", state="running"))
    db.commit()

    graph = build_topology(db)
    assert _ids(graph).count("image:nginx:latest") == 1
    assert "image:nginx" not in _ids(graph)


def test_tailscale_edges_and_exit_node(db):
    online = _host(1, tailscale_ipv4="100.64.0.1", tailscale_online=True, tailscale_exit_node=True)
    offline = _host(2, tailscale_ipv4="100.64.0.2", tailscale_online=False)
    not_enrolled = _host(3)
    db.add_all([online, offline, not_enrolled])
    db.commit()

    graph = build_topology(db)
    assert "tailnet" in _ids(graph)
    assert "ts-online" in _edge(graph, "host:host-1", "tailnet")["classes"]
    assert "exit-node" in _node(graph, "host:host-1")["classes"]
    assert "ts-offline" in _edge(graph, "host:host-2", "tailnet")["classes"]
    assert not [
        e for e in graph["edges"]
        if e["data"]["source"] == "host:host-3" and e["data"]["target"] == "tailnet"
    ]


def test_exposure_data_on_host_node(db):
    host = _host(1)
    db.add(host)
    db.flush()
    db.add(ListeningPort(host_id=host.id, proto="tcp", address="0.0.0.0", port=22, process="sshd"))
    db.add(ListeningPort(host_id=host.id, proto="tcp", address="127.0.0.1", port=5432, process="postgres"))
    db.add(TailscaleService(host_id=host.id, proto="https", port=443, target="127.0.0.1:8080", funnel=True))
    db.commit()

    graph = build_topology(db)
    node = _node(graph, "host:host-1")
    assert node["data"]["exposed_ports"] == 1  # loopback listener not counted
    assert node["data"]["funnel"] is True
    assert "exposed" in node["classes"]
    assert any(p["port"] == 22 for p in node["data"]["ports"])


def test_k8s_membership_edge_carries_role(db):
    host = _host(1)
    cluster = K8sCluster(name="prod")
    db.add_all([host, cluster])
    db.flush()
    db.add(K8sNode(host_id=host.id, cluster_id=cluster.id, role=K8sNodeRole.CONTROL_PLANE))
    db.commit()

    graph = build_topology(db)
    edge = _edge(graph, "host:host-1", "cluster:prod")
    assert edge["data"]["role"] == "control-plane"


def test_subnet_derivation_groups_lan_hosts(db):
    db.add_all(
        [
            _host(1, primary_ipv4="192.168.1.10"),
            _host(2, primary_ipv4="192.168.1.20"),
            _host(3, primary_ipv4="10.0.0.5"),
        ]
    )
    db.commit()

    graph = build_topology(db)
    assert "subnet:192.168.1.0/24" in _ids(graph)
    assert "subnet:10.0.0.0/24" in _ids(graph)
    _edge(graph, "host:host-1", "subnet:192.168.1.0/24")
    _edge(graph, "host:host-2", "subnet:192.168.1.0/24")


def test_node_ids_unique(db):
    host = _host(1, primary_ipv4="192.168.1.10", tailscale_ipv4="100.64.0.1", tailscale_online=True)
    cluster = K8sCluster(name="prod")
    db.add_all([host, cluster])
    db.flush()
    db.add(Container(host_id=host.id, name="a", image="app:1", compose_project="s"))
    db.add(Container(host_id=host.id, name="b", image="app:1", compose_project="s"))
    # two workload rows collapsing to one node must not collide with anything
    db.add(_workload(cluster.id, "ns", "pod-1", "w", "w:1"))
    db.add(_workload(cluster.id, "ns", "pod-2", "w", "w:1"))
    db.commit()

    graph = build_topology(db)
    ids = _ids(graph)
    assert len(ids) == len(set(ids))
    # two containers share one image node
    assert ids.count("image:app:1") == 1
    assert graph["meta"]["hosts"] == 1
    assert graph["meta"]["containers"] == 2


def _workload(cluster_id, namespace, pod_name, container_name, image, canonical="__raw__"):
    return K8sWorkload(
        cluster_id=cluster_id,
        namespace=namespace,
        pod_name=pod_name,
        container_name=container_name,
        image=image,
        image_canonical=image if canonical == "__raw__" else canonical,
    )


def test_k8s_workload_replicas_deduped_into_namespace_compound(db):
    cluster = K8sCluster(name="prod")
    db.add(cluster)
    db.flush()
    for pod in ("falco-2kx9p", "falco-7hh2m", "falco-9qwzr"):
        db.add(_workload(cluster.id, "security", pod, "falco", "falco:1"))
    db.commit()

    graph = build_topology(db)
    wl = _node(graph, "workload:prod/security/falco@falco:1")
    assert wl["data"]["replicas"] == 3
    assert wl["data"]["label"] == "falco ×3"
    assert wl["data"]["parent"] == "ns:prod/security"
    assert wl["data"]["namespace"] == "security"
    assert wl["data"]["cluster"] == "prod"
    assert "layer-k8s" in wl["classes"]

    ns = _node(graph, "ns:prod/security")
    assert "layer-k8s" in ns["classes"]
    _edge(graph, "cluster:prod", "ns:prod/security")

    # the cluster hexagon stays a plain infra node — never a compound parent
    cluster_node = _node(graph, "cluster:prod")
    assert "k8s" in cluster_node["classes"].split()
    assert "layer-infra" in cluster_node["classes"]
    assert not [n for n in graph["nodes"] if n["data"].get("parent") == "cluster:prod"]


def test_k8s_only_image_gets_dual_layer_node_and_edge(db):
    cluster = K8sCluster(name="prod")
    db.add(cluster)
    db.flush()
    db.add(_workload(cluster.id, "apps", "web-1", "web", "lonely:1"))
    db.commit()

    graph = build_topology(db)
    img = _node(graph, "image:lonely:1")
    classes = img["classes"].split()
    assert "layer-images" in classes
    assert "layer-k8s" in classes

    edge = _edge(graph, "workload:prod/apps/web@lonely:1", "image:lonely:1")
    edge_classes = edge["classes"].split()
    assert "layer-images" in edge_classes
    assert "layer-k8s" in edge_classes
    assert "runs" in edge_classes
    assert any(c.startswith("sev-") for c in edge_classes)


def test_image_shared_with_container_stays_single_layer(db):
    host = _host(1)
    cluster = K8sCluster(name="prod")
    db.add_all([host, cluster])
    db.flush()
    db.add(Container(host_id=host.id, name="app", image="shared:1", state="running"))
    db.add(_workload(cluster.id, "apps", "app-1", "app", "shared:1"))
    db.commit()

    graph = build_topology(db)
    assert _ids(graph).count("image:shared:1") == 1
    classes = _node(graph, "image:shared:1")["classes"].split()
    assert "layer-images" in classes
    assert "layer-k8s" not in classes
    # both a docker runs edge and a k8s runs edge point at the shared image
    _edge(graph, "container:host-1/app", "image:shared:1")
    _edge(graph, "workload:prod/apps/app@shared:1", "image:shared:1")


def test_digest_only_workload_has_no_image_node_or_edge(db):
    cluster = K8sCluster(name="prod")
    db.add(cluster)
    db.flush()
    raw = "repo/svc@sha256:" + "a" * 64
    db.add(_workload(cluster.id, "apps", "svc-1", "svc", raw, canonical=None))
    db.commit()

    graph = build_topology(db)
    wl = _node(graph, f"workload:prod/apps/svc@{raw}")
    assert wl["data"]["image"] == raw
    assert not [n for n in graph["nodes"] if n["data"]["kind"] == "image"]
    assert not [e for e in graph["edges"] if e["data"]["kind"] == "runs"]


def test_meta_reports_workload_and_namespace_counts(db):
    cluster = K8sCluster(name="prod")
    db.add(cluster)
    db.flush()
    db.add(_workload(cluster.id, "security", "falco-1", "falco", "falco:1"))
    db.add(_workload(cluster.id, "security", "falco-2", "falco", "falco:1"))  # same node
    db.add(_workload(cluster.id, "apps", "web-1", "web", "web:1"))
    db.commit()

    graph = build_topology(db)
    assert graph["meta"]["workloads"] == 2  # falco (deduped) + web
    assert graph["meta"]["namespaces"] == 2  # security + apps
