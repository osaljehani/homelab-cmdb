from datetime import datetime

from cmdb.domain.models import (
    Container,
    Host,
    Image,
    ImageScan,
    K8sCluster,
    K8sNode,
    K8sNodeRole,
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
    db.add(host)
    db.flush()
    db.add(Container(host_id=host.id, name="a", image="app:1", compose_project="s"))
    db.add(Container(host_id=host.id, name="b", image="app:1", compose_project="s"))
    db.commit()

    graph = build_topology(db)
    ids = _ids(graph)
    assert len(ids) == len(set(ids))
    # two containers share one image node
    assert ids.count("image:app:1") == 1
    assert graph["meta"]["hosts"] == 1
    assert graph["meta"]["containers"] == 2
