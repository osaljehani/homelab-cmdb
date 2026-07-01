import json
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import Image, ImageScan, ImportSource
from cmdb.domain.services.trivy_import import import_scan_run, import_from_path


def _report(ref, vulns, os_name="debian 12.4"):
    return {
        "SchemaVersion": 2,
        "ArtifactName": ref,
        "ArtifactType": "container_image",
        "Metadata": {
            "ImageID": "sha256:img",
            "RepoDigests": [f"{ref.split(':')[0]}@sha256:dig"],
        },
        "Results": [
            {
                "Target": f"{ref} ({os_name})",
                "Class": "os-pkgs",
                "Type": "debian",
                "Vulnerabilities": vulns,
            }
        ],
    }


@pytest.fixture
def envelope() -> dict:
    return {
        "host": "dev-workstation",
        "scanned_at": "2026-07-01T04:00:00Z",
        "trivy_version": "0.72.0",
        "images": [
            _report(
                "nginx:latest",
                [
                    {
                        "VulnerabilityID": "CVE-2023-1",
                        "PkgName": "libssl3",
                        "InstalledVersion": "3.0.11-1",
                        "FixedVersion": "3.0.11-2",
                        "Severity": "HIGH",
                        "Title": "openssl issue",
                    },
                    {
                        "VulnerabilityID": "CVE-2023-2",
                        "PkgName": "zlib1g",
                        "InstalledVersion": "1.2.13",
                        "FixedVersion": "",
                        "Severity": "CRITICAL",
                        "Title": "zlib issue",
                    },
                ],
            ),
            _report("redis:7", []),  # Results present but no vulns
        ],
    }


def test_import_creates_image_scan_and_vulns(db: Session, envelope):
    counts = import_scan_run(db, envelope)
    assert counts["images"] == 2
    assert counts["vulnerabilities"] == 2
    nginx = db.query(Image).filter_by(ref="nginx:latest").first()
    assert nginx is not None
    assert nginx.digest == "nginx@sha256:dig"
    assert nginx.last_scanned_at == datetime(2026, 7, 1, 4, 0, 0)
    scan = nginx.scans[0]
    assert (scan.critical, scan.high, scan.total) == (1, 1, 2)
    assert scan.trivy_version == "0.72.0"
    fixed = {v.vuln_id: v.fixed_version for v in scan.vulnerabilities}
    assert fixed["CVE-2023-1"] == "3.0.11-2"
    assert fixed["CVE-2023-2"] == ""  # no fix available


def test_reimport_appends_history_and_preserves_noisy(db: Session, envelope):
    import_scan_run(db, envelope)
    nginx = db.query(Image).filter_by(ref="nginx:latest").first()
    nginx.expected_noisy = True
    db.flush()

    envelope["scanned_at"] = "2026-07-02T04:00:00Z"
    import_scan_run(db, envelope)

    nginx = db.query(Image).filter_by(ref="nginx:latest").first()
    assert len(nginx.scans) == 2  # history appended
    assert nginx.expected_noisy is True  # NOT reset by importer
    assert db.query(Image).count() == 2  # no duplicate image rows


def test_result_with_null_vulnerabilities(db: Session):
    env = {
        "scanned_at": "2026-07-01T04:00:00Z",
        "trivy_version": "0.72.0",
        "images": [
            {
                "ArtifactName": "busybox:1",
                "Results": [{"Target": "busybox", "Class": "os-pkgs"}],
            }
        ],
    }
    counts = import_scan_run(db, env)
    assert counts["vulnerabilities"] == 0
    assert db.query(ImageScan).one().total == 0


def test_missing_images_key_raises(db: Session):
    with pytest.raises(ValueError, match="images"):
        import_scan_run(db, {"scanned_at": "2026-07-01T04:00:00Z"})


def test_unknown_severity_bucketed_as_unknown(db: Session):
    env = {
        "scanned_at": "2026-07-01T04:00:00Z",
        "images": [_report("x:1", [{"VulnerabilityID": "CVE-z", "Severity": "WEIRD"}])],
    }
    import_scan_run(db, env)
    assert db.query(ImageScan).one().unknown == 1


def test_import_from_path_single_file(db: Session, tmp_path, envelope):
    f = tmp_path / "scan.json"
    f.write_text(json.dumps(envelope))
    log = import_from_path(db, str(f), ImportSource.CLI)
    assert log.images_scanned == 2
    assert log.vulnerabilities_upserted == 2
    assert log.source == ImportSource.CLI
    assert log.notes is None


def test_import_from_path_bad_report_non_fatal(db: Session, tmp_path):
    env = {
        "scanned_at": "2026-07-01T04:00:00Z",
        "images": [{"no_artifact_name": True}, _report("ok:1", [])],
    }
    f = tmp_path / "scan.json"
    f.write_text(json.dumps(env))
    log = import_from_path(db, str(f), ImportSource.CLI)
    assert log.images_scanned == 1  # the good one
    assert log.notes is not None and "image[0]" in log.notes
