from fastapi.testclient import TestClient

from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.hosts import get_host
from cmdb.web.app import app
from cmdb.web.deps import get_db_dep


def _client(db):
    app.dependency_overrides[get_db_dep] = lambda: db
    return TestClient(app)


def test_notes_roundtrip_via_web(db, host_facts):
    client = _client(db)
    try:
        import_host(db, host_facts)
        r = client.post("/hosts/testhost/notes", data={"notes": "rack 2"})
        assert r.status_code == 200
        assert "rack 2" in r.text
        assert get_host(db, "testhost").notes == "rack 2"

        r = client.get("/hosts/testhost")
        assert "rack 2" in r.text
    finally:
        app.dependency_overrides.clear()


def test_notes_unknown_host_404(db):
    client = _client(db)
    try:
        r = client.post("/hosts/ghost/notes", data={"notes": "x"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_custom_fields_add_and_remove_via_web(db, host_facts):
    client = _client(db)
    try:
        import_host(db, host_facts)
        r = client.post(
            "/hosts/testhost/fields", data={"key": "owner", "value": "alice"}
        )
        assert r.status_code == 200
        assert "owner" in r.text and "alice" in r.text
        assert get_host(db, "testhost").custom_fields == {"owner": "alice"}

        r = client.delete("/hosts/testhost/fields/owner")
        assert r.status_code == 200
        assert "alice" not in r.text
        assert get_host(db, "testhost").custom_fields == {}
    finally:
        app.dependency_overrides.clear()


def test_blank_field_key_is_ignored(db, host_facts):
    client = _client(db)
    try:
        import_host(db, host_facts)
        r = client.post("/hosts/testhost/fields", data={"key": " ", "value": "x"})
        assert r.status_code == 200
        assert not get_host(db, "testhost").custom_fields
    finally:
        app.dependency_overrides.clear()
