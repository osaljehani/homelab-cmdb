import json

from fastapi.testclient import TestClient

from cmdb.web.app import app
from cmdb.web.deps import get_db_dep


def _client(db):
    app.dependency_overrides[get_db_dep] = lambda: db
    return TestClient(app)


def _envelope():
    return {
        "scanned_at": "2026-07-01T04:00:00Z", "trivy_version": "0.72.0",
        "images": [{
            "ArtifactName": "nginx:latest", "Metadata": {"ImageID": "sha256:x"},
            "Results": [{"Target": "nginx", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-1", "Severity": "CRITICAL", "PkgName": "libc"}]}],
        }],
    }


def test_trivy_upload_then_images_page(db):
    client = _client(db)
    try:
        files = {"files": ("scan.json", json.dumps(_envelope()), "application/json")}
        r = client.post("/import/upload/trivy", files=files)
        assert r.status_code == 200
        assert "1 images" in r.text or "nginx" in r.text

        r = client.get("/images/")
        assert r.status_code == 200
        assert "nginx:latest" in r.text
    finally:
        app.dependency_overrides.clear()


def test_dashboard_has_vuln_panel(db):
    client = _client(db)
    try:
        client.post("/import/upload/trivy",
                    files={"files": ("s.json", json.dumps(_envelope()), "application/json")})
        r = client.get("/")
        assert r.status_code == 200
        assert "Vulnerabilities" in r.text
    finally:
        app.dependency_overrides.clear()


def test_image_detail_and_noisy_toggle(db):
    client = _client(db)
    try:
        client.post("/import/upload/trivy",
                    files={"files": ("s.json", json.dumps(_envelope()), "application/json")})

        r = client.get("/images/nginx:latest")
        assert r.status_code == 200
        assert "CVE-1" in r.text          # vuln listed
        assert "libc" in r.text           # package listed

        r = client.post("/images/nginx:latest/noisy", data={"on": "true"},
                        follow_redirects=False)
        assert r.status_code in (302, 303)
        from cmdb.domain.services.images import get_image
        assert get_image(db, "nginx:latest").expected_noisy is True
    finally:
        app.dependency_overrides.clear()


def _seed_k8s_placement(db):
    from datetime import datetime

    from cmdb.domain.models import Image, ImageScan, K8sCluster, K8sWorkload

    img = Image(ref="portfolio:0.0.1", first_seen=datetime(2026, 7, 1))
    db.add(img)
    db.flush()
    db.add(
        ImageScan(
            image_id=img.id,
            scanned_at=datetime(2026, 7, 3),
            source="kubernetes",
            total=0,
        )
    )
    cluster = K8sCluster(name="demo-cluster")
    db.add(cluster)
    db.flush()
    db.add(
        K8sWorkload(
            cluster_id=cluster.id,
            namespace="web",
            pod_name="portfolio-abc",
            container_name="main",
            image="portfolio:0.0.1",
            image_canonical="portfolio:0.0.1",
        )
    )
    db.commit()


def test_images_page_upgrades_registry_badge_to_cluster_placement(db):
    client = _client(db)
    try:
        _seed_k8s_placement(db)
        r = client.get("/images/")
        assert r.status_code == 200
        assert "demo-cluster" in r.text
        assert "registry only" not in r.text

        r = client.get("/images/portfolio:0.0.1")
        assert "demo-cluster" in r.text
    finally:
        app.dependency_overrides.clear()


def test_image_detail_shows_scan_host(db):
    client = _client(db)
    try:
        env = _envelope()
        env["host"] = "scanner-1"
        client.post("/import/upload/trivy",
                    files={"files": ("s.json", json.dumps(env), "application/json")})
        r = client.get("/images/nginx:latest")
        assert r.status_code == 200
        assert "scanner-1" in r.text
    finally:
        app.dependency_overrides.clear()


def test_image_detail_404_for_unknown(db):
    client = _client(db)
    try:
        r = client.get("/images/ghost:1")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def _envelope_at(ts, images):
    """A trivy scan-run envelope at timestamp `ts` covering the given image refs."""
    return {
        "scanned_at": ts, "trivy_version": "0.72.0",
        "images": [{
            "ArtifactName": ref, "Metadata": {"ImageID": "sha256:x"},
            "Results": [{"Target": ref, "Vulnerabilities": [
                {"VulnerabilityID": "CVE-1", "Severity": "CRITICAL", "PkgName": "libc"}]}],
        } for ref in images],
    }


def _upload(client, envelope):
    return client.post(
        "/import/upload/trivy",
        files={"files": ("s.json", json.dumps(envelope), "application/json")},
    )


def test_delete_image_removes_and_redirects_with_counts(db):
    client = _client(db)
    try:
        _upload(client, _envelope())
        r = client.post("/images/nginx:latest/delete", follow_redirects=False)
        assert r.status_code in (302, 303)
        assert "deleted=nginx:latest" in r.headers["location"]
        from cmdb.domain.services.images import get_image
        assert get_image(db, "nginx:latest") is None
    finally:
        app.dependency_overrides.clear()


def test_delete_unknown_image_404(db):
    client = _client(db)
    try:
        r = client.post("/images/ghost:1/delete", follow_redirects=False)
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_deleted_notice_renders_on_list(db):
    client = _client(db)
    try:
        r = client.get("/images/?deleted=nginx:latest&scans=2&vulns=5")
        assert r.status_code == 200
        assert "nginx:latest" in r.text
        assert "Removed" in r.text
    finally:
        app.dependency_overrides.clear()


def _zot_envelope(ref):
    """A registry (zot) scan-run envelope -> classified as kubernetes source."""
    return {
        "host": "zot-registry", "scanned_at": "2026-07-02T04:30:00Z",
        "trivy_version": "zot-embedded",
        "images": [{
            "ArtifactName": ref, "Metadata": {"ImageID": "sha256:x"},
            "Results": [{"Target": ref, "Vulnerabilities": []}],
        }],
    }


def test_deployed_column_shows_docker_placement_and_k8s_tag(db):
    from cmdb.domain.models import Container, Host

    client = _client(db)
    try:
        # A Docker-scanned image that also has a collected container on testhost.
        _upload(client, _envelope_at("2026-07-01T04:00:00Z", ["gitea/gitea:latest"]))
        host = Host(machine_id="m1", hostname="testhost")
        db.add(host)
        db.flush()
        db.add(Container(host_id=host.id, name="gitea", image="gitea/gitea:latest"))
        # A registry (k8s) image with no matching container.
        _upload(client, _zot_envelope("portfolio:0.0.1"))
        db.flush()

        r = client.get("/images/")
        assert r.status_code == 200
        gitea_row = r.text.split("gitea/gitea:latest", 1)[1].split("</tr>", 1)[0]
        assert "testhost / gitea" in gitea_row
        portfolio_row = r.text.split("portfolio:0.0.1", 1)[1].split("</tr>", 1)[0]
        assert "registry only" in portfolio_row
    finally:
        app.dependency_overrides.clear()


def test_stale_badge_marks_dropped_image_only(db):
    client = _client(db)
    try:
        # First run: both images present.
        _upload(client, _envelope_at("2026-07-01T04:00:00Z", ["nginx:latest", "old:1"]))
        # Second, newer run: old:1 has dropped out.
        _upload(client, _envelope_at("2026-07-05T04:00:00Z", ["nginx:latest"]))

        r = client.get("/images/")
        assert r.status_code == 200
        # A crude but reliable check: the stale badge sits on old:1's row, not nginx's.
        assert "stale" in r.text
        old_row = r.text.split("old:1", 1)[1].split("</tr>", 1)[0]
        nginx_row = r.text.split("nginx:latest", 1)[1].split("</tr>", 1)[0]
        assert "stale" in old_row
        assert "stale" not in nginx_row
    finally:
        app.dependency_overrides.clear()


def test_stale_badge_not_set_by_other_source_run(db):
    client = _client(db)
    try:
        # Docker run at 07-01, then a NEWER registry (zot) run at 07-02 that
        # naturally doesn't cover the docker-scanned image.
        _upload(client, _envelope_at("2026-07-01T04:00:00Z", ["nginx:latest"]))
        _upload(client, _zot_envelope("portfolio:0.0.1"))  # 2026-07-02

        r = client.get("/images/")
        assert r.status_code == 200
        nginx_row = r.text.split("nginx:latest", 1)[1].split("</tr>", 1)[0]
        assert "stale" not in nginx_row
    finally:
        app.dependency_overrides.clear()


def test_images_tabs_filter_running_and_registry_only(db):
    from cmdb.domain.models import Container, Host

    client = _client(db)
    try:
        _upload(client, _envelope_at("2026-07-01T04:00:00Z", ["gitea/gitea:latest"]))
        host = Host(machine_id="m1", hostname="testhost")
        db.add(host)
        db.flush()
        db.add(
            Container(
                host_id=host.id,
                name="gitea",
                image="gitea/gitea:latest",
                state="running",
            )
        )
        _upload(client, _zot_envelope("portfolio:0.0.1"))
        db.flush()

        r = client.get("/images/?deployed=running")
        assert r.status_code == 200
        assert "gitea/gitea:latest" in r.text
        assert "portfolio:0.0.1" not in r.text

        r = client.get("/images/?deployed=registry-only")
        assert r.status_code == 200
        assert "portfolio:0.0.1" in r.text
        assert "gitea/gitea:latest" not in r.text

        r = client.get("/images/")
        assert "gitea/gitea:latest" in r.text and "portfolio:0.0.1" in r.text
    finally:
        app.dependency_overrides.clear()


def test_dashboard_tracks_running_and_notes_registry_only_exclusion(db):
    client = _client(db)
    try:
        _upload(client, _envelope())
        r = client.get("/")
        assert r.status_code == 200
        assert "Running ·" in r.text
        # Registry-only images get a muted exclusion note, not a severity bar.
        assert "Registry-only ·" not in r.text
        assert "registry-only image" in r.text
    finally:
        app.dependency_overrides.clear()
