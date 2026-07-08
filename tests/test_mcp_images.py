import pytest

from cmdb.domain.services.trivy_import import import_scan_run


@pytest.fixture
def seeded(db):
    import_scan_run(
        db,
        {
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
    return db


def test_image_tools(seeded, monkeypatch):
    from contextlib import contextmanager
    import cmdb.mcp.server as server

    @contextmanager
    def _fake_session():
        yield seeded

    monkeypatch.setattr(server, "get_session", _fake_session)

    summaries = server.list_image_scans()
    assert any(s.ref == "nginx:latest" and s.high == 1 for s in summaries)

    detail = server.image_vulnerabilities("nginx:latest")
    assert detail.ref == "nginx:latest"
    assert detail.vulnerabilities[0].vuln_id == "CVE-1"

    roll = server.vuln_summary()
    assert roll.high == 1 and roll.scanned_images == 1

    out = server.set_image_noisy("nginx:latest", True)
    assert out.expected_noisy is True
    # Now excluded from the rollup.
    assert server.vuln_summary().scanned_images == 0


def test_image_tools_expose_stale_and_deployment_status(seeded, monkeypatch):
    from contextlib import contextmanager
    import cmdb.mcp.server as server
    from cmdb.domain.models import Container, Host

    @contextmanager
    def _fake_session():
        yield seeded

    monkeypatch.setattr(server, "get_session", _fake_session)

    host = Host(machine_id="m1", hostname="testhost")
    seeded.add(host)
    seeded.flush()
    seeded.add(
        Container(host_id=host.id, name="web", image="nginx:latest", state="running")
    )
    seeded.flush()

    s = next(x for x in server.list_image_scans() if x.ref == "nginx:latest")
    assert s.stale is False
    assert s.deployment_status == "running"
    assert s.running_on == ["testhost/web"]

    detail = server.image_vulnerabilities("nginx:latest")
    assert detail.stale is False
    assert detail.deployment_status == "running"
    assert detail.running_on == ["testhost/web"]

    roll = server.vuln_summary()
    assert roll.running.high == 1 and roll.running.scanned_images == 1
    assert roll.registry_only.images == 0


def test_delete_image_requires_confirmation(seeded, monkeypatch):
    from contextlib import contextmanager
    import cmdb.mcp.server as server

    @contextmanager
    def _fake_session():
        yield seeded

    monkeypatch.setattr(server, "get_session", _fake_session)

    # Without confirm=True it must delete nothing.
    res = server.delete_image("nginx:latest")
    assert res["deleted"] is False
    assert server.image_vulnerabilities("nginx:latest").ref == "nginx:latest"

    # With confirm=True it deletes the image + its scan history.
    res = server.delete_image("nginx:latest", confirm=True)
    assert res["deleted"] is True
    assert res["scans"] == 1 and res["vulnerabilities"] == 1
    with pytest.raises(ValueError, match="not found"):
        server.image_vulnerabilities("nginx:latest")


def test_delete_image_missing_raises_when_confirmed(seeded, monkeypatch):
    from contextlib import contextmanager
    import cmdb.mcp.server as server

    @contextmanager
    def _fake_session():
        yield seeded

    monkeypatch.setattr(server, "get_session", _fake_session)
    with pytest.raises(ValueError, match="not found"):
        server.delete_image("ghost:1", confirm=True)
