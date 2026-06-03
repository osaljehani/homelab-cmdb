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
