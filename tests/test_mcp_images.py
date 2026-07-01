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
