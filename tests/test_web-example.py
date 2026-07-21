import pytest
from fastapi.testclient import TestClient

from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.hosts import add_tag
from cmdb.web.app import app
from cmdb.web import deps


@pytest.fixture(autouse=True)
def override_db(db):
    app.dependency_overrides[deps.get_db_dep] = lambda: db
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def populated_client(client, db, host_facts):
    import_host(db, host_facts)
    return client


def test_dashboard_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "HomeLabCMDB" in r.text


def test_dashboard_shows_host_count(populated_client):
    r = populated_client.get("/")
    assert r.status_code == 200
    assert "1" in r.text


def test_hosts_list_loads(populated_client):
    r = populated_client.get("/hosts")
    assert r.status_code == 200
    assert "testhost" in r.text


def test_hosts_list_search(populated_client):
    r = populated_client.get("/hosts", params={"q": "testhost"})
    assert "testhost" in r.text
    r2 = populated_client.get("/hosts", params={"q": "zzznomatch"})
    assert "testhost" not in r2.text


def test_host_detail_loads(populated_client):
    r = populated_client.get("/hosts/testhost")
    assert r.status_code == 200
    assert "testhost" in r.text
    assert "192.168.1.10" in r.text


def test_host_detail_404(client):
    r = client.get("/hosts/ghost")
    assert r.status_code == 404


def test_host_tag_add(populated_client, db):
    r = populated_client.post("/hosts/testhost/tags", data={"tag": "proxmox"})
    assert r.status_code == 200
    assert "proxmox" in r.text


def test_host_tag_remove(populated_client, db):
    add_tag(db, "testhost", "proxmox")
    r = populated_client.delete("/hosts/testhost/tags/proxmox")
    assert r.status_code == 200
    assert "proxmox" not in r.text


def test_dashboard_shows_security_panel(populated_client):
    r = populated_client.get("/")
    assert r.status_code == 200
    assert "Security posture" in r.text


def test_hosts_list_has_security_column(populated_client):
    r = populated_client.get("/hosts")
    assert "Security" in r.text
    # sample host has AppArmor enabled -> hardened, shows the MAC name
    assert "AppArmor" in r.text


def test_topology_page_has_kubernetes_layer_toggle(client):
    r = client.get("/topology")
    assert r.status_code == 200
    assert 'data-layer="layer-k8s"' in r.text


def test_dashboard_lists_exposed_host(client, db, host_facts):
    host_facts["ansible_facts"]["ansible_hostname"] = "exposedhost"
    host_facts["ansible_facts"]["ansible_apparmor"] = {"status": "disabled"}
    host_facts["ansible_facts"]["ansible_selinux"] = {"status": "disabled"}
    import_host(db, host_facts)
    r = client.get("/")
    assert "exposedhost" in r.text
    assert "No MAC active" in r.text


def _stub_facts_capturing(monkeypatch, captured):
    """Replace the route's collect_facts with one that records its inventory arg."""
    from pathlib import Path

    from cmdb.domain.models import ImportLog, ImportSource
    from cmdb.web.routes import collect as collect_route

    def fake_facts(db, inventory, limit, source):
        captured["inventory"] = inventory
        captured["text"] = Path(inventory).read_text() if inventory else None
        return ImportLog(
            source=ImportSource.COLLECT, filename="stub",
            hosts_upserted=1, hosts_failed=0, containers_upserted=0,
        )

    monkeypatch.setattr(collect_route, "collect_facts", fake_facts)


def test_collect_page_form_available_without_env_inventory(client, monkeypatch):
    from cmdb.web.routes import collect as collect_route
    monkeypatch.setattr(collect_route.settings, "ansible_inventory", None)

    r = client.get("/collect/")

    assert r.status_code == 200
    assert 'action="/collect/run"' in r.text
    assert "database" in r.text.lower()


def test_collect_run_uses_uploaded_inventory(client, monkeypatch):
    from pathlib import Path
    captured: dict = {}
    _stub_facts_capturing(monkeypatch, captured)

    r = client.post(
        "/collect/run",
        data={"mode": "facts"},
        files={"inventory": ("hosts.yml", b"all:\n  hosts:\n    uphost: {}\n", "application/x-yaml")},
    )

    assert r.status_code == 200
    assert "uphost" in captured["text"]
    # uploaded inventory temp file is cleaned up after the run
    assert not Path(captured["inventory"]).exists()


def test_collect_run_without_file_defers_to_service(client, monkeypatch):
    captured: dict = {}
    _stub_facts_capturing(monkeypatch, captured)

    r = client.post("/collect/run", data={"mode": "facts"})

    assert r.status_code == 200
    # No upload -> None passed through so the service generates from the DB.
    assert captured["inventory"] is None


def test_collect_run_k8s_mode_invokes_k8s_collector(client, monkeypatch):
    from cmdb.domain.models import ImportLog, ImportSource
    from cmdb.web.routes import collect as collect_route

    called: dict = {}

    def fake_k8s(db, inventory, limit, source):
        called["yes"] = True
        return ImportLog(
            source=ImportSource.COLLECT, filename="stub k8s",
            hosts_upserted=0, hosts_failed=0,
            k8s_clusters_upserted=1, k8s_nodes_upserted=2, k8s_namespaces_upserted=3,
        )

    monkeypatch.setattr(collect_route, "collect_k8s", fake_k8s)

    r = client.post("/collect/run", data={"mode": "k8s"})

    assert r.status_code == 200
    assert called.get("yes")
    assert "K8s collection" in r.text


def test_collect_run_tailscale_mode(populated_client, monkeypatch):
    import cmdb.web.routes.collect as collect_route
    from cmdb.domain.models import ImportLog, ImportSource

    def fake(db, inv, limit, source):
        return ImportLog(source=ImportSource.COLLECT, filename="collect tailscale",
                         tailscale_services_upserted=2, hosts_upserted=1)

    monkeypatch.setattr(collect_route, "collect_tailscale", fake)
    r = populated_client.post("/collect/run", data={"mode": "tailscale", "limit": ""})
    assert r.status_code == 200
    assert "Tailscale collection" in r.text
    assert "2 exposed services" in r.text


def test_collect_run_ports_mode(populated_client, monkeypatch):
    import cmdb.web.routes.collect as collect_route
    from cmdb.domain.models import ImportLog, ImportSource

    def fake(db, inv, limit, source):
        return ImportLog(source=ImportSource.COLLECT, filename="collect ports",
                         listening_ports_upserted=5)

    monkeypatch.setattr(collect_route, "collect_ports", fake)
    r = populated_client.post("/collect/run", data={"mode": "ports", "limit": ""})
    assert r.status_code == 200
    assert "Ports collection" in r.text
    assert "5 listening ports" in r.text


def test_host_detail_shows_tailscale_and_ports(populated_client, db):
    from cmdb.domain.models import Host, ListeningPort, TailscaleService

    host = db.query(Host).filter_by(hostname="testhost").one()
    host.tailscale_ipv4 = "100.64.0.1"
    host.tailscale_dns_name = "host-a.example-tailnet.ts.net"
    db.add(ListeningPort(host_id=host.id, proto="tcp", address="0.0.0.0",
                         port=22, process="sshd"))
    db.add(TailscaleService(host_id=host.id, proto="https", port=443,
                            target="127.0.0.1:8080", funnel=True))
    db.commit()

    r = populated_client.get("/hosts/testhost")
    assert r.status_code == 200
    assert "100.64.0.1" in r.text       # tailscale card
    assert "sshd" in r.text             # listening ports table
    assert "443" in r.text              # tailscale services table


def test_import_page_loads(client):
    r = client.get("/import")
    assert r.status_code == 200
    assert "Import" in r.text


def test_import_upload_single_file(client, db, host_facts):
    import json
    content = json.dumps(host_facts).encode()
    r = client.post(
        "/import/upload",
        files={"files": ("testhost", content, "application/json")},
    )
    assert r.status_code == 200
    assert "1" in r.text
