import json
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import Image, ImageScan, ImportSource
from cmdb.domain.services.trivy_import import (
    _derive_source,
    import_scan_run,
    import_from_path,
)


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
        "host": "testhost",
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


@pytest.mark.parametrize(
    "host, trivy_version, expected",
    [
        ("dev-workstation", "0.72.0", "docker"),
        ("testhost", "0.72.0", "docker"),
        ("zot-registry", "zot-embedded", "kubernetes"),
        ("some-registry", "registry-embedded", "kubernetes"),
        (None, "0.72.0", "docker"),
    ],
)
def test_derive_source(host, trivy_version, expected):
    assert _derive_source(host, trivy_version) == expected


def test_import_persists_envelope_host(db: Session, envelope):
    import_scan_run(db, envelope)
    scan = db.query(ImageScan).first()
    assert scan.host == "testhost"


def test_import_without_host_stores_none(db: Session):
    env = {"scanned_at": "2026-07-01T04:00:00Z", "images": [_report("redis:7", [])]}
    import_scan_run(db, env)
    assert db.query(ImageScan).one().host is None


def test_import_persists_docker_source(db: Session, envelope):
    # envelope fixture: host=testhost, trivy_version=0.72.0 -> runtime Docker scan
    import_scan_run(db, envelope)
    scan = db.query(Image).filter_by(ref="nginx:latest").first().scans[0]
    assert scan.source == "docker"


def test_import_persists_kubernetes_source_from_zot(db: Session):
    env = {
        "host": "zot-registry",
        "scanned_at": "2026-07-02T04:30:00Z",
        "trivy_version": "zot-embedded",
        "images": [_report("portfolio:0.0.1", [])],
    }
    import_scan_run(db, env)
    assert db.query(ImageScan).one().source == "kubernetes"


def test_explicit_envelope_source_overrides_derivation(db: Session):
    env = {
        "host": "dev-workstation",  # would derive "docker"
        "source": "kubernetes",
        "scanned_at": "2026-07-02T04:30:00Z",
        "trivy_version": "0.72.0",
        "images": [_report("x:1", [])],
    }
    import_scan_run(db, env)
    assert db.query(ImageScan).one().source == "kubernetes"


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


def test_duplicate_cve_across_results_counted_once(db: Session):
    # Standalone trivy emits one Result per Go binary, so the same stdlib CVE
    # can appear under many Targets; it must count once per (CVE, pkg, version).
    vuln = {
        "VulnerabilityID": "CVE-2025-1",
        "PkgName": "stdlib",
        "InstalledVersion": "1.24.0",
        "Severity": "HIGH",
    }
    report = {
        "ArtifactName": "multibin:1",
        "Results": [
            {"Target": "usr/bin/a", "Class": "lang-pkgs", "Vulnerabilities": [dict(vuln)]},
            {"Target": "usr/bin/b", "Class": "lang-pkgs", "Vulnerabilities": [dict(vuln)]},
            {
                "Target": "usr/bin/c",
                "Class": "lang-pkgs",
                # different installed version -> a distinct finding, kept
                "Vulnerabilities": [dict(vuln, InstalledVersion="1.25.0")],
            },
        ],
    }
    env = {"scanned_at": "2026-07-21T04:00:00Z", "images": [report]}
    counts = import_scan_run(db, env)
    assert counts["vulnerabilities"] == 2
    scan = db.query(ImageScan).one()
    assert (scan.high, scan.total) == (2, 2)
    assert {v.installed_version for v in scan.vulnerabilities} == {"1.24.0", "1.25.0"}


def test_runtime_and_registry_refs_dedup_to_one_image(db: Session):
    # runtime scan spells it short; Zot scan keeps the library/ prefix
    import_scan_run(
        db,
        {
            "host": "dev-workstation",
            "scanned_at": "2026-07-01T04:00:00Z",
            "trivy_version": "0.72.0",
            "images": [_report("memcached:1.6.29-alpine", [])],
        },
    )
    import_scan_run(
        db,
        {
            "host": "zot-registry",
            "scanned_at": "2026-07-01T04:30:00Z",
            "trivy_version": "zot-embedded",
            "images": [_report("library/memcached:1.6.29-alpine", [])],
        },
    )
    imgs = db.query(Image).filter(Image.ref == "memcached:1.6.29-alpine").all()
    assert len(imgs) == 1  # one canonical row, not two
    assert db.query(Image).count() == 1
    assert len(imgs[0].scans) == 2  # both source scans interleaved on it


def test_import_from_path_writes_daily_snapshot(db: Session, tmp_path, envelope):
    from cmdb.domain.models import VulnSnapshot

    f = tmp_path / "scan.json"
    f.write_text(json.dumps(envelope))
    import_from_path(db, str(f), ImportSource.CLI)

    rows = {s.image_ref: s for s in db.query(VulnSnapshot).all()}
    assert set(rows) == {"nginx:latest", "redis:7"}
    assert all(s.snapshot_date == datetime.utcnow().date() for s in rows.values())
    # no containers/workloads in this db -> nothing counts as running
    assert all(s.was_running is False for s in rows.values())
    assert rows["nginx:latest"].critical == 1
    assert rows["nginx:latest"].high == 1


def test_import_from_path_same_day_rerun_keeps_one_snapshot_row(
    db: Session, tmp_path, envelope
):
    from cmdb.domain.models import VulnSnapshot

    f = tmp_path / "scan.json"
    f.write_text(json.dumps(envelope))
    import_from_path(db, str(f), ImportSource.CLI)
    import_from_path(db, str(f), ImportSource.CLI)

    assert db.query(VulnSnapshot).filter_by(image_ref="nginx:latest").count() == 1
