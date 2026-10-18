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


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_static_session_js_served(client):
    r = client.get("/static/js/session.js")
    assert r.status_code == 200


def test_base_page_loads_session_guard(client):
    r = client.get("/")
    assert "/static/js/session.js" in r.text


def test_static_css_served(client):
    r = client.get("/static/css/main.css")
    assert r.status_code == 200
    assert "--bg-page" in r.text


def test_static_htmx_served(client):
    r = client.get("/static/js/htmx.min.js")
    assert r.status_code == 200


def test_no_cdn_references(client):
    """UI must work offline: no external font/script hosts in any page."""
    r = client.get("/")
    assert "fonts.googleapis.com" not in r.text
    assert "fonts.gstatic.com" not in r.text
    assert "unpkg.com" not in r.text


def test_brand_links_home(client):
    r = client.get("/")
    assert '<a href="/" class="brand">' in r.text


def test_stat_cards_link_to_pages(populated_client):
    r = populated_client.get("/")
    for target in ("/hosts", "/containers", "/k8s"):
        assert f'class="stat-card" href="{target}"' in r.text


def test_hosts_row_has_real_link(populated_client):
    r = populated_client.get("/hosts")
    assert 'href="/hosts/testhost"' in r.text


def test_host_detail_has_breadcrumbs(populated_client):
    r = populated_client.get("/hosts/testhost")
    assert 'class="breadcrumbs"' in r.text


def test_dashboard_renders_svg_charts(populated_client, db):
    from datetime import datetime, timedelta
    from cmdb.domain.models import Container, Host, Image, ImageScan

    img = Image(ref="app:1", first_seen=datetime.utcnow())
    db.add(img)
    db.flush()
    # The trend tracks running images only, so the image needs a container.
    host = db.query(Host).filter_by(hostname="testhost").one()
    db.add(Container(host_id=host.id, name="app", image="app:1", state="running"))
    db.add_all(
        [
            ImageScan(image_id=img.id, scanned_at=datetime.utcnow() - timedelta(days=3), critical=2, total=5),
            ImageScan(image_id=img.id, scanned_at=datetime.utcnow(), critical=1, total=3),
        ]
    )
    db.commit()
    # The trend reads vuln_snapshots (written on import); reconstruct them for
    # the directly-seeded scans above.
    from cmdb.domain.services.vuln_snapshots import backfill_snapshots

    backfill_snapshots(db)
    db.commit()
    r = populated_client.get("/")
    assert r.status_code == 200
    assert "<svg" in r.text  # vuln trend sparkline
    assert "Fleet freshness" in r.text
    assert "Recent changes" in r.text


def test_dashboard_vuln_card_tracks_running_images_only(populated_client, db):
    from datetime import datetime
    from cmdb.domain.models import Container, Host, Image, ImageScan

    host = db.query(Host).filter_by(hostname="testhost").one()
    running = Image(ref="app:1", first_seen=datetime.utcnow())
    registry = Image(ref="reg:1", first_seen=datetime.utcnow())
    db.add_all([running, registry])
    db.flush()
    db.add(Container(host_id=host.id, name="app", image="app:1", state="running"))
    db.add_all(
        [
            ImageScan(image_id=running.id, scanned_at=datetime.utcnow(), critical=1, total=1),
            ImageScan(image_id=registry.id, scanned_at=datetime.utcnow(), critical=9, total=9),
        ]
    )
    db.commit()
    r = populated_client.get("/")
    assert r.status_code == 200
    # The old second severity bar is gone; a muted exclusion note remains.
    assert "Registry-only ·" not in r.text
    assert "1 registry-only image excluded" in r.text
    assert "Running ·" in r.text


def test_dashboard_no_registry_only_note_when_none_excluded(populated_client, db):
    from datetime import datetime
    from cmdb.domain.models import Container, Host, Image, ImageScan

    host = db.query(Host).filter_by(hostname="testhost").one()
    running = Image(ref="app:1", first_seen=datetime.utcnow())
    db.add(running)
    db.flush()
    db.add(Container(host_id=host.id, name="app", image="app:1", state="running"))
    db.add(ImageScan(image_id=running.id, scanned_at=datetime.utcnow(), critical=1, total=1))
    db.commit()
    r = populated_client.get("/")
    assert "registry-only image" not in r.text


def test_dashboard_stale_host_listed(populated_client, db):
    from datetime import datetime, timedelta
    from cmdb.domain.models import Host

    host = db.query(Host).filter_by(hostname="testhost").one()
    host.last_seen = datetime.utcnow() - timedelta(days=30)
    db.commit()
    r = populated_client.get("/")
    assert "stale" in r.text.lower()
    assert "testhost" in r.text


def test_settings_page_loads(client):
    r = client.get("/settings/")
    assert r.status_code == 200


def test_topology_page_loads(client):
    r = client.get("/topology/")
    assert r.status_code == 200
    assert 'id="cy"' in r.text
    assert "cytoscape.min.js" in r.text


def test_topology_page_has_kubernetes_layer_toggle(client):
    r = client.get("/topology")
    assert r.status_code == 200
    assert 'data-layer="layer-k8s"' in r.text


def test_topology_data_json(populated_client):
    r = populated_client.get("/topology/data")
    assert r.status_code == 200
    graph = r.json()
    assert set(graph) == {"nodes", "edges", "meta"}
    assert any(n["data"]["id"] == "host:testhost" for n in graph["nodes"])


def test_nav_has_topology_link(client):
    r = client.get("/")
    assert 'href="/topology"' in r.text


def test_search_full_page(populated_client):
    r = populated_client.get("/search", params={"gq": "testhost"})
    assert r.status_code == 200
    assert "<html" in r.text  # full page, not a partial
    assert 'href="/hosts/testhost"' in r.text


def test_search_dropdown_partial_for_htmx(populated_client):
    r = populated_client.get(
        "/search", params={"gq": "testhost"}, headers={"HX-Request": "true"}
    )
    assert r.status_code == 200
    assert "<html" not in r.text  # partial only
    assert 'href="/hosts/testhost"' in r.text


def test_search_no_results_shows_empty_state(client):
    r = client.get("/search", params={"gq": "zzznomatch"})
    assert r.status_code == 200
    assert 'class="empty-state"' in r.text


def test_nav_has_global_search_input(client):
    r = client.get("/")
    assert 'name="gq"' in r.text


def test_empty_states_show_guidance(client):
    for path, cta in [
        ("/hosts", "/collect"),
        ("/containers", "/import"),
        ("/images", "/import"),
    ]:
        r = client.get(path)
        assert r.status_code == 200
        assert 'class="empty-state"' in r.text, path
        assert cta in r.text, path


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
            source=ImportSource.COLLECT,
            filename="stub",
            hosts_upserted=1,
            hosts_failed=0,
            containers_upserted=0,
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
        files={
            "inventory": (
                "hosts.yml",
                b"all:\n  hosts:\n    uphost: {}\n",
                "application/x-yaml",
            )
        },
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
            source=ImportSource.COLLECT,
            filename="stub k8s",
            hosts_upserted=0,
            hosts_failed=0,
            k8s_clusters_upserted=1,
            k8s_nodes_upserted=2,
            k8s_namespaces_upserted=3,
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
        return ImportLog(
            source=ImportSource.COLLECT,
            filename="collect tailscale",
            tailscale_services_upserted=2,
            hosts_upserted=1,
        )

    monkeypatch.setattr(collect_route, "collect_tailscale", fake)
    r = populated_client.post("/collect/run", data={"mode": "tailscale", "limit": ""})
    assert r.status_code == 200
    assert "Tailscale collection" in r.text
    assert "2 exposed services" in r.text


def test_collect_run_ports_mode(populated_client, monkeypatch):
    import cmdb.web.routes.collect as collect_route
    from cmdb.domain.models import ImportLog, ImportSource

    def fake(db, inv, limit, source):
        return ImportLog(
            source=ImportSource.COLLECT,
            filename="collect ports",
            listening_ports_upserted=5,
        )

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
    db.add(
        ListeningPort(
            host_id=host.id, proto="tcp", address="0.0.0.0", port=22, process="sshd"
        )
    )
    db.add(
        TailscaleService(
            host_id=host.id,
            proto="https",
            port=443,
            target="127.0.0.1:8080",
            funnel=True,
        )
    )
    db.commit()

    r = populated_client.get("/hosts/testhost")
    assert r.status_code == 200
    assert "100.64.0.1" in r.text  # tailscale card
    assert "sshd" in r.text  # listening ports table
    assert "443" in r.text  # tailscale services table


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
