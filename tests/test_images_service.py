from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import Image, ImageScan
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
    _img(db, "hexstrike:latest", noisy=True)
    assert {i.ref for i in images_svc.list_images(db)} == {"nginx:latest", "hexstrike:latest"}
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
    _img(db, "hexstrike:latest")
    img = images_svc.set_noisy(db, "hexstrike:latest", True)
    assert img.expected_noisy is True
    with pytest.raises(ValueError, match="not found"):
        images_svc.set_noisy(db, "ghost:1", True)


def test_vuln_summary_excludes_noisy_and_uses_latest(db: Session):
    _img(db, "nginx:latest", scans=[(datetime(2026, 7, 1), 1, 1), (datetime(2026, 7, 3), 2, 3)])
    _img(db, "hexstrike:latest", noisy=True, scans=[(datetime(2026, 7, 3), 99, 99)])
    _img(db, "unscanned:1")  # no scans
    s = images_svc.vuln_summary(db)
    assert s["images"] == 2          # non-noisy images
    assert s["scanned_images"] == 1  # only nginx has a scan
    assert s["critical"] == 2 and s["high"] == 3 and s["total"] == 5
