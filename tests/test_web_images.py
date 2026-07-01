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
