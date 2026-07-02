from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import Image, ImageScan, Vulnerability
from cmdb.domain.services import images as images_svc


def _img(db, ref, noisy=False, scans=()):
    img = Image(ref=ref, expected_noisy=noisy, first_seen=datetime.utcnow())
    db.add(img)
    db.flush()
    for when, crit, high in scans:
        db.add(
            ImageScan(
                image_id=img.id,
                scanned_at=when,
                critical=crit,
                high=high,
                total=crit + high,
            )
        )
    db.flush()
    return img


def test_list_images_excludes_noisy_when_requested(db: Session):
    _img(db, "nginx:latest")
    _img(db, "noisy-tool:latest", noisy=True)
    assert {i.ref for i in images_svc.list_images(db)} == {"nginx:latest", "noisy-tool:latest"}
    assert {i.ref for i in images_svc.list_images(db, include_noisy=False)} == {"nginx:latest"}


def test_latest_scan_picks_most_recent(db: Session):
    img = _img(db, "nginx:latest", scans=[
        (datetime(2026, 7, 1), 1, 0),
        (datetime(2026, 7, 3), 5, 2),
    ])
    latest = images_svc.latest_scan(db, img)
    assert latest.scanned_at == datetime(2026, 7, 3)
    assert latest.critical == 5


def test_set_noisy_toggles_and_raises_on_missing(db: Session):
    _img(db, "noisy-tool:latest")
    img = images_svc.set_noisy(db, "noisy-tool:latest", True)
    assert img.expected_noisy is True
    with pytest.raises(ValueError, match="not found"):
        images_svc.set_noisy(db, "ghost:1", True)


def test_delete_image_removes_image_and_scan_history(db: Session):
    img = _img(db, "gone:1", scans=[(datetime(2026, 7, 1), 1, 1), (datetime(2026, 7, 3), 2, 3)])
    db.add(Vulnerability(scan_id=img.scans[0].id, vuln_id="CVE-1", severity="HIGH"))
    db.flush()

    result = images_svc.delete_image(db, "gone:1")

    assert result == {"ref": "gone:1", "scans": 2, "vulnerabilities": 1}
    assert images_svc.get_image(db, "gone:1") is None
    assert db.query(ImageScan).filter_by(image_id=img.id).count() == 0
    assert db.query(Vulnerability).count() == 0


def test_delete_image_raises_on_missing(db: Session):
    with pytest.raises(ValueError, match="not found"):
        images_svc.delete_image(db, "ghost:1")


def test_vuln_summary_excludes_noisy_and_uses_latest(db: Session):
    _img(db, "nginx:latest", scans=[(datetime(2026, 7, 1), 1, 1), (datetime(2026, 7, 3), 2, 3)])
    _img(db, "noisy-tool:latest", noisy=True, scans=[(datetime(2026, 7, 3), 99, 99)])
    _img(db, "unscanned:1")  # no scans
    s = images_svc.vuln_summary(db)
    assert s["images"] == 2          # non-noisy images
    assert s["scanned_images"] == 1  # only nginx has a scan
    assert s["critical"] == 2 and s["high"] == 3 and s["total"] == 5
