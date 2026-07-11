from fastapi.testclient import TestClient

from cmdb.domain.models import Host
from cmdb.web.app import app
from cmdb.web.deps import get_db_dep


def _client(db):
    app.dependency_overrides[get_db_dep] = lambda: db
    return TestClient(app)


def _host(db, i, ip=None, mac=None, gateway=None):
    h = Host(
        machine_id=f"m{i:032d}",
        hostname=f"host-{i}",
        primary_ipv4=ip,
        primary_mac=mac,
        gateway=gateway,
    )
    db.add(h)
    db.flush()
    return h


def test_network_page_groups_and_flags(db):
    client = _client(db)
    try:
        _host(db, 1, ip="192.168.1.10", gateway="192.168.1.1")
        _host(db, 2, ip="192.168.1.10")  # duplicate IP
        _host(db, 3, ip="10.0.0.5")
        db.commit()

        r = client.get("/network/")
        assert r.status_code == 200
        assert "192.168.1.0/24" in r.text
        assert "10.0.0.0/24" in r.text
        assert "duplicate IP" in r.text
    finally:
        app.dependency_overrides.clear()


def test_network_page_empty_state(db):
    client = _client(db)
    try:
        r = client.get("/network/")
        assert r.status_code == 200
        assert "No network data yet" in r.text
    finally:
        app.dependency_overrides.clear()


def test_dashboard_shows_network_summary(db):
    client = _client(db)
    try:
        _host(db, 1, ip="192.168.1.10")
        db.commit()
        r = client.get("/")
        assert r.status_code == 200
        assert "Network" in r.text and "subnet" in r.text
    finally:
        app.dependency_overrides.clear()
