from datetime import datetime

from sqlalchemy.orm import Session

from cmdb.domain.models import Image, ImageScan, Vulnerability, ImportLog


def test_image_scan_vuln_relationships(db: Session):
    img = Image(ref="nginx:latest", digest="sha256:abc", first_seen=datetime.utcnow())
    db.add(img)
    db.flush()
    assert img.expected_noisy is False  # default

    scan = ImageScan(image_id=img.id, scanned_at=datetime.utcnow(), trivy_version="0.72.0",
                     critical=1, high=2, total=3)
    db.add(scan)
    db.flush()
    scan.vulnerabilities.append(
        Vulnerability(vuln_id="CVE-2023-1", pkg_name="libssl3", severity="HIGH",
                      installed_version="3.0.11-1", fixed_version="3.0.11-2")
    )
    db.flush()

    assert img.scans[0].total == 3
    assert img.scans[0].vulnerabilities[0].vuln_id == "CVE-2023-1"


def test_cascade_delete_scan_removes_vulns(db: Session):
    img = Image(ref="redis:7", first_seen=datetime.utcnow())
    db.add(img)
    db.flush()
    scan = ImageScan(image_id=img.id, scanned_at=datetime.utcnow())
    scan.vulnerabilities.append(Vulnerability(vuln_id="CVE-x", severity="LOW"))
    db.add(scan)
    db.flush()
    db.delete(scan)
    db.flush()
    assert db.query(Vulnerability).count() == 0


def test_import_log_has_new_columns(db: Session):
    log = ImportLog(source="cli", images_scanned=2, vulnerabilities_upserted=9)
    db.add(log)
    db.flush()
    assert log.images_scanned == 2
    assert log.vulnerabilities_upserted == 9
