"""Data-backfill tests for the vuln_snapshots migration.

Seeds the ORM tables (conftest's ``db`` creates the full schema, including
``vuln_snapshots``), then invokes the migration's ``backfill(bind)`` directly —
same pattern as test_canonicalize_migration.py.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from cmdb.db.migrations.versions.a1d2e3f4b5c6_add_vuln_snapshots import backfill
from cmdb.domain.models import (
    Container,
    Host,
    Image,
    ImageScan,
    VulnSnapshot,
)

DAY1 = datetime(2026, 7, 1, 4, 0, 0)
DAY2 = datetime(2026, 7, 2, 4, 0, 0)


def _img(db, ref, noisy=False):
    img = Image(ref=ref, expected_noisy=noisy, first_seen=DAY1)
    db.add(img)
    db.flush()
    return img


def _scan(db, img, when, crit, total):
    db.add(ImageScan(image_id=img.id, scanned_at=when, critical=crit, total=total))
    db.flush()


def _running_container(db, image_ref):
    host = db.query(Host).first()
    if host is None:
        host = Host(machine_id="m1", hostname="testhost")
        db.add(host)
        db.flush()
    db.add(Container(host_id=host.id, name=image_ref, image=image_ref, state="running"))
    db.flush()


def test_backfill_carries_latest_scan_forward_with_flags(db: Session):
    app = _img(db, "app:1")
    _scan(db, app, DAY1, 5, 7)
    _scan(db, app, DAY2, 1, 1)
    _running_container(db, "app:1")
    reg = _img(db, "registry-only:1")
    _scan(db, reg, DAY1, 3, 3)
    noisy = _img(db, "noisy:1", noisy=True)
    _scan(db, noisy, DAY1, 9, 9)

    backfill(db.connection())
    db.expire_all()

    rows = {
        (str(s.snapshot_date), s.image_ref): s for s in db.query(VulnSnapshot).all()
    }
    # day1: all three images; day2: app's newer scan carried the others forward
    assert len(rows) == 6
    assert rows[("2026-07-01", "app:1")].critical == 5
    assert rows[("2026-07-02", "app:1")].critical == 1  # latest, not summed
    assert rows[("2026-07-02", "registry-only:1")].critical == 3  # carried forward
    assert rows[("2026-07-01", "app:1")].was_running is True
    assert rows[("2026-07-01", "registry-only:1")].was_running is False
    assert rows[("2026-07-01", "noisy:1")].was_noisy is True


def test_backfill_skips_days_that_already_have_rows(db: Session):
    app = _img(db, "app:1")
    _scan(db, app, DAY1, 5, 5)
    db.add(
        VulnSnapshot(
            snapshot_date=DAY1.date(),
            image_ref="app:1",
            was_running=True,
            was_noisy=False,
            critical=5,
            total=5,
        )
    )
    db.flush()

    backfill(db.connection())
    db.expire_all()

    assert db.query(VulnSnapshot).count() == 1  # untouched, not duplicated


def test_backfill_empty_db_is_noop(db: Session):
    backfill(db.connection())
    assert db.query(VulnSnapshot).count() == 0
