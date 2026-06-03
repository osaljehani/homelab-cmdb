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
def populated_client(client, db, blade14_facts):
    import_host(db, blade14_facts)
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
    assert "blade14" in r.text


def test_hosts_list_search(populated_client):
    r = populated_client.get("/hosts", params={"q": "blade14"})
    assert "blade14" in r.text
    r2 = populated_client.get("/hosts", params={"q": "zzznomatch"})
    assert "blade14" not in r2.text
