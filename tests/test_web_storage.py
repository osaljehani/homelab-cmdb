from fastapi.testclient import TestClient

from cmdb.domain.models import Host
from cmdb.web.app import app
from cmdb.web.deps import get_db_dep


def _client(db):
    app.dependency_overrides[get_db_dep] = lambda: db
    return TestClient(app)


def _host_with_mount(db, available, total=100 * 2**30):
    h = Host(
        machine_id="m1",
        hostname="testhost",
        raw_facts={
            "ansible_mounts": [
                {
                    "mount": "/",
                    "device": "/dev/sda1",
                    "fstype": "ext4",
                    "size_total": total,
                    "size_available": available,
                }
            ],
            "ansible_devices": {
                "sda": {"model": "Demo SSD", "size": "100 GB", "rotational": "0"}
            },
        },
    )
    db.add(h)
    db.commit()
    return h


def test_host_detail_shows_storage_card(db):
    client = _client(db)
    try:
        _host_with_mount(db, available=40 * 2**30)
        r = client.get("/hosts/testhost")
        assert r.status_code == 200
        assert "Storage" in r.text
        assert "Demo SSD" in r.text
        assert "60%" in r.text
    finally:
        app.dependency_overrides.clear()


def test_dashboard_storage_warning_panel(db):
    client = _client(db)
    try:
        _host_with_mount(db, available=2 * 2**30)  # 98% used
        r = client.get("/")
        assert r.status_code == 200
        assert "Storage warnings" in r.text
        assert "98%" in r.text
    finally:
        app.dependency_overrides.clear()


def test_dashboard_no_storage_panel_when_healthy(db):
    client = _client(db)
    try:
        _host_with_mount(db, available=40 * 2**30)
        r = client.get("/")
        assert "Storage warnings" not in r.text
    finally:
        app.dependency_overrides.clear()
