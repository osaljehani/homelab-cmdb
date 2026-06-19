import json
import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import Container, ImportSource
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.docker_import import (
    import_containers,
    import_from_path,
)


@pytest.fixture
def populated_db(db: Session, host_facts: dict) -> Session:
    import_host(db, host_facts)
    db.commit()
    return db


@pytest.fixture
def docker_data() -> dict:
    return {
        "host": "testhost",
        "containers": [
            {
                "name": "nginx",
                "image": "nginx:latest",
                "status": "Up 3 days",
                "state": "running",
                "ports": "0.0.0.0:80->80/tcp",
                "compose_project": "web",
            },
            {
                "name": "redis",
                "image": "redis:7",
                "status": "Exited (0) 2 hours ago",
                "state": "exited",
            },
        ],
    }


# --- import_containers ---


def test_import_creates_containers(populated_db, docker_data):
    counts = import_containers(populated_db, docker_data)
    assert counts["containers"] == 2
    names = {c.name for c in populated_db.query(Container).all()}
    assert names == {"nginx", "redis"}


def test_import_maps_fields(populated_db, docker_data):
    import_containers(populated_db, docker_data)
    nginx = populated_db.query(Container).filter_by(name="nginx").first()
    assert nginx.image == "nginx:latest"
    assert nginx.state == "running"
    assert nginx.ports == "0.0.0.0:80->80/tcp"
    assert nginx.compose_project == "web"


def test_import_replace_removes_stale(populated_db, docker_data):
    import_containers(populated_db, docker_data)
    # Re-import without redis it should disappear (replace-on-import).
    docker_data["containers"] = [docker_data["containers"][0]]
    import_containers(populated_db, docker_data)
    names = {c.name for c in populated_db.query(Container).all()}
    assert names == {"nginx"}


def test_import_unknown_host_raises(populated_db):
    with pytest.raises(ValueError, match="not found"):
        import_containers(populated_db, {"host": "ghost", "containers": []})


def test_import_missing_host_key_raises(populated_db):
    with pytest.raises(ValueError, match="'host'"):
        import_containers(populated_db, {"containers": []})


def test_import_docker_ps_raw_format(populated_db):
    """Accept raw `docker ps --format '{{json .}}'` field names + label string."""
    data = {
        "host": "testhost",
        "containers": [
            {
                "Names": "grafana",
                "Image": "grafana/grafana",
                "Status": "Up 1 day",
                "State": "running",
                "Ports": "3000/tcp",
                "Labels": "com.docker.compose.project=monitoring,foo=bar",
            }
        ],
    }
    import_containers(populated_db, data)
    c = populated_db.query(Container).filter_by(name="grafana").first()
    assert c is not None
    assert c.image == "grafana/grafana"
    assert c.compose_project == "monitoring"


def test_import_matches_host_by_fqdn(db: Session, host_facts: dict):
    host_facts["ansible_facts"]["ansible_hostname"] = "test-node-1"
    host_facts["ansible_facts"]["ansible_fqdn"] = "test-node-1.local"
    import_host(db, host_facts)
    db.commit()
    counts = import_containers(
        db, {"host": "test-node-1.local", "containers": [{"name": "x"}]}
    )
    assert counts["containers"] == 1


# --- import_from_path ---


def test_import_from_path_single_file(populated_db, tmp_path, docker_data):
    f = tmp_path / "docker.json"
    f.write_text(json.dumps(docker_data))
    log = import_from_path(populated_db, str(f), ImportSource.CLI)
    assert log.containers_upserted == 2
    assert log.source == ImportSource.CLI
    assert log.notes is None


def test_import_from_path_unknown_host_non_fatal(populated_db, tmp_path, docker_data):
    bad = {"host": "ghost", "containers": [{"name": "x"}]}
    (tmp_path / "good.json").write_text(json.dumps(docker_data))
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    log = import_from_path(populated_db, str(tmp_path), ImportSource.CLI)
    assert log.containers_upserted == 2
    assert log.notes is not None
    assert "ghost" in log.notes


def test_import_from_path_array(populated_db, tmp_path):
    data = [{"host": "testhost", "containers": [{"name": "a"}, {"name": "b"}]}]
    f = tmp_path / "docker.json"
    f.write_text(json.dumps(data))
    log = import_from_path(populated_db, str(f), ImportSource.CLI)
    assert log.containers_upserted == 2
