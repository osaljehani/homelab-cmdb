"""Daily vulnerability snapshots: immutable history for the dashboard trend.

A snapshot row freezes, per image per day, the latest-scan severity rollup and
that day's classification (running / noisy). The dashboard trend reads these
rows instead of live-joining current state, so deleting a remediated image (or
an image simply leaving the running set) no longer rewrites past points.

Writers replace the whole day (delete + insert) rather than upserting — the
codebase's replace-on-import idiom — so repeated same-day imports stay
duplicate-free and a deleted image drops out of today's point automatically.
All functions take a Session and an injectable ``now``; datetimes are naive
UTC, matching the models.
"""

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from cmdb.domain.models import Image, ImageScan, VulnSnapshot
from cmdb.domain.services.images import image_overview

_SEVERITY_KEYS = ("critical", "high", "medium", "low", "unknown", "total")


def write_daily_snapshot(session: Session, now: datetime | None = None) -> int:
    """Replace today's vuln_snapshots rows from current per-image state.

    Classification (running vs registry-only, noisy) comes from
    :func:`image_overview` — the exact placement logic ``vuln_summary`` uses,
    so snapshot and summary can never disagree. Images without any scan are
    skipped. Returns the number of rows written.
    """
    now = now or datetime.utcnow()
    today = now.date()
    session.query(VulnSnapshot).filter(VulnSnapshot.snapshot_date == today).delete(
        synchronize_session=False
    )

    written = 0
    for row in image_overview(session, include_noisy=True):
        scan = row["scan"]
        if scan is None:
            continue
        session.add(
            VulnSnapshot(
                snapshot_date=today,
                image_ref=row["image"].ref,
                was_running=row["status"] == "running",
                was_noisy=row["image"].expected_noisy,
                scanned_at=scan.scanned_at,
                **{key: getattr(scan, key) or 0 for key in _SEVERITY_KEYS},
            )
        )
        written += 1
    session.flush()
    return written


def snapshot_trend(
    session: Session, days: int = 30, now: datetime | None = None
) -> list[dict]:
    """Per-day severity totals for running, non-noisy images.

    One point per snapshot date in the window; days without snapshots are
    simply absent (the sparkline plots points evenly by index, as before).
    Point shape matches the old live ``vuln_trend`` so templates are untouched.
    """
    now = now or datetime.utcnow()
    window_start = (now - timedelta(days=days)).date()
    rows = (
        session.query(
            VulnSnapshot.snapshot_date,
            *(func.sum(getattr(VulnSnapshot, key)) for key in _SEVERITY_KEYS),
        )
        .filter(VulnSnapshot.snapshot_date >= window_start)
        .filter(VulnSnapshot.was_running.is_(True))
        .filter(VulnSnapshot.was_noisy.is_(False))
        .group_by(VulnSnapshot.snapshot_date)
        .order_by(VulnSnapshot.snapshot_date)
        .all()
    )
    return [
        {
            "date": date,
            **{key: value or 0 for key, value in zip(_SEVERITY_KEYS, sums)},
        }
        for date, *sums in rows
    ]


def backfill_snapshots(session: Session, now: datetime | None = None) -> int:
    """Best-effort reconstruction of past daily rows from ImageScan history.

    For each distinct scan date with no existing snapshot rows: latest scan per
    image on-or-before that date (carry-forward, like the old live trend),
    with running/noisy flags taken from *current* state — historical placement
    was never recorded, so today's classification is the best available
    approximation. Idempotent: dates that already have rows are left alone.
    Returns the number of rows written.
    """
    now = now or datetime.utcnow()
    scans = session.query(ImageScan).order_by(ImageScan.scanned_at).all()
    if not scans:
        return 0

    images = {img.id: img for img in session.query(Image).all()}
    running_refs = {
        row["image"].ref
        for row in image_overview(session, include_noisy=True)
        if row["status"] == "running"
    }
    existing_dates = {
        d for (d,) in session.query(VulnSnapshot.snapshot_date).distinct()
    }

    written = 0
    for day in sorted({s.scanned_at.date() for s in scans}):
        if day in existing_dates:
            continue
        latest: dict[int, ImageScan] = {}
        for s in scans:  # ascending order: last write wins
            if s.scanned_at.date() <= day:
                latest[s.image_id] = s
        for image_id, scan in latest.items():
            image = images.get(image_id)
            if image is None:
                continue
            session.add(
                VulnSnapshot(
                    snapshot_date=day,
                    image_ref=image.ref,
                    was_running=image.ref in running_refs,
                    was_noisy=image.expected_noisy,
                    scanned_at=scan.scanned_at,
                    **{key: getattr(scan, key) or 0 for key in _SEVERITY_KEYS},
                )
            )
            written += 1
    session.flush()
    return written
