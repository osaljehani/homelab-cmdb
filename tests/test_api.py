import json

from fastapi.testclient import TestClient

from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.hosts import add_tag
from cmdb.domain.services.k8s import add_cluster
from cmdb.domain.services.trivy_import import import_scan_run
from cmdb.web.app import app
from cmdb.web.deps import get_db_dep


def _client(db):
    app.dependency_overrides[get_db_dep] = lambda: db
    return TestClient(app)


def _seed_image(db):
    import_scan_run(
        db,
        {
            "host": "testhost",
            "scanned_at": "2026-07-01T04:00:00Z",
            "trivy_version": "0.72.0",
            "images": [
                {
                    "ArtifactName": "nginx:latest",
                    "Metadata": {"ImageID": "sha256:x"},
                    "Results": [
                        {
                            "Target": "nginx",
                            "Vulnerabilities": [
                                {
                                    "VulnerabilityID": "CVE-1",
                                    "Severity": "HIGH",
                                    "PkgName": "libssl3",
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    db.commit()


def test_hosts_list_and_filter(db, host_facts):
    client = _client(db)
    try:
        import_host(db, host_facts)
        add_tag(db, "testhost", "lab")

        r = client.get("/api/v1/hosts")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["hostname"] == "testhost"
        assert data[0]["tags"] == ["lab"]
        assert "raw_facts" not in data[0]

        assert client.get("/api/v1/hosts?tag=lab").json()
        assert client.get("/api/v1/hosts?tag=nope").json() == []
    finally:
        app.dependency_overrides.clear()


def test_host_detail_and_json_404(db, host_facts):
    client = _client(db)
    try:
        import_host(db, host_facts)

        r = client.get("/api/v1/hosts/testhost")
        assert r.status_code == 200
        assert r.json()["hostname"] == "testhost"
        assert "containers" in r.json()

        r = client.get("/api/v1/hosts/ghost")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")
        assert "detail" in r.json()
    finally:
        app.dependency_overrides.clear()


def test_containers_flat_list(db, host_facts):
    client = _client(db)
    try:
        import_host(db, host_facts)
        from cmdb.domain.services.docker_import import import_containers

        import_containers(
            db,
            {
                "host": "testhost",
                "containers": [
                    {"name": "web", "image": "nginx:latest", "state": "running"}
                ],
            },
        )
        r = client.get("/api/v1/containers")
        assert r.status_code == 200
        assert r.json() == [
            {
                "host": "testhost",
                "name": "web",
                "image": "nginx:latest",
                "status": None,
                "state": "running",
                "ports": None,
                "compose_project": None,
            }
        ]
    finally:
        app.dependency_overrides.clear()


def test_clusters(db):
    client = _client(db)
    try:
        add_cluster(db, "demo-cluster", "test cluster")
        r = client.get("/api/v1/clusters")
        assert r.status_code == 200
        assert r.json()[0]["name"] == "demo-cluster"
        assert r.json()[0]["node_count"] == 0
    finally:
        app.dependency_overrides.clear()


def test_images_and_detail(db):
    client = _client(db)
    try:
        _seed_image(db)

        r = client.get("/api/v1/images")
        assert r.status_code == 200
        img = r.json()[0]
        assert img["ref"] == "nginx:latest"
        assert img["high"] == 1
        assert img["scan_host"] == "testhost"

        r = client.get("/api/v1/images/nginx:latest")
        assert r.status_code == 200
        assert r.json()["vulnerabilities"][0]["vuln_id"] == "CVE-1"

        assert client.get("/api/v1/images/ghost:1").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_vuln_summary(db):
    client = _client(db)
    try:
        _seed_image(db)
        r = client.get("/api/v1/vuln-summary")
        assert r.status_code == 200
        assert r.json()["high"] == 1
        assert r.json()["registry_only"]["images"] == 1
    finally:
        app.dependency_overrides.clear()


def test_openapi_docs_include_api(db):
    client = _client(db)
    try:
        spec = client.get("/openapi.json")
        assert spec.status_code == 200
        paths = spec.json()["paths"]
        assert "/api/v1/hosts" in paths
    finally:
        app.dependency_overrides.clear()


def test_mcp_schemas_shim_still_imports():
    # cmdb.mcp.schemas moved to cmdb.domain.schemas with a re-export shim
    from cmdb.domain import schemas as domain_schemas
    from cmdb.mcp import schemas as mcp_schemas

    assert mcp_schemas.HostOut is domain_schemas.HostOut
    assert json is not None  # keep the import honest
