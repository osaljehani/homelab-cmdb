"""Aggregations for the dashboard: freshness, vuln trend, change feed, OS mix.

All functions take a Session and return plain dicts/lists; `now` is
injectable for tests. Datetimes are naive UTC, matching the models.
"""

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, HostSnapshot, Image, ImageScan, ImportLog
from cmdb.domain.services.history import diff_snapshots

# ImportLog counters worth surfacing in the change feed.
_IMPORT_COUNTERS = (
    "hosts_upserted",
    "hosts_failed",
    "containers_upserted",
    "k8s_clusters_upserted",
    "k8s_nodes_upserted",
    "k8s_namespaces_upserted",
    "k8s_workloads_upserted",
    "tailscale_services_upserted",
    "listening_ports_upserted",
    "images_scanned",
    "vulnerabilities_upserted",
)


def fleet_freshness(
    session: Session, stale_days: int = 7, now: datetime | None = None
) -> dict:
    """Bucket hosts by data age (fresh/stale/never) and tailscale presence."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=stale_days)
    hosts = session.query(Host).order_by(Host.hostname).all()

    out = {
        "total": len(hosts),
        "fresh": 0,
        "stale": 0,
        "never": 0,
        "stale_hosts": [],
        "ts_online": 0,
        "ts_offline": 0,
        "ts_absent": 0,
    }
    for h in hosts:
        if h.last_seen is None:
            out["never"] += 1
        elif h.last_seen <= cutoff:
            out["stale"] += 1
            out["stale_hosts"].append(h)
        else:
            out["fresh"] += 1

        if h.tailscale_online is True:
            out["ts_online"] += 1
        elif h.tailscale_online is False:
            out["ts_offline"] += 1
        else:
            out["ts_absent"] += 1
    return out


def vuln_trend(
    session: Session,
    days: int = 30,
    now: datetime | None = None,
    image_ids: set[int] | None = None,
) -> list[dict]:
    """Fleet-wide severity totals per day, over the last `days` days.

    One point per day that has at least one scan; each point sums, per
    non-noisy image, the latest scan on-or-before that day. O(days x images),
    fine at homelab scale.

    `image_ids` restricts the trend to those images (None means all). Running
    status is a current-state property, so a trend filtered to the running set
    reflects today's placements — a stopped container's history drops out.
    """
    now = now or datetime.utcnow()
    window_start = now - timedelta(days=days)
    q = (
        session.query(ImageScan)
        .join(Image)
        .filter(Image.expected_noisy.is_(False))
        .filter(ImageScan.scanned_at >= window_start)
    )
    if image_ids is not None:
        q = q.filter(ImageScan.image_id.in_(image_ids))
    scans = q.order_by(ImageScan.scanned_at).all()
    if not scans:
        return []

    scan_days = sorted({s.scanned_at.date() for s in scans})
    points = []
    for day in scan_days:
        # latest scan per image on-or-before this day
        latest: dict[int, ImageScan] = {}
        for s in scans:
            if s.scanned_at.date() <= day:
                latest[s.image_id] = s  # scans are ordered ascending
        point = {"date": day, "critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        for s in latest.values():
            for key in ("critical", "high", "medium", "low", "total"):
                point[key] += getattr(s, key) or 0
        points.append(point)
    return points


def recent_changes(session: Session, limit: int = 10) -> list[dict]:
    """Merged newest-first feed of host config changes and import runs.

    Snapshot entries: {kind: "snapshot", at, hostname, changes, initial}.
    Import entries: {kind: "import", at, source, filename, counts} where
    counts holds only the non-zero ImportLog counters.
    """
    feed: list[dict] = []

    snapshots = (
        session.query(HostSnapshot)
        .order_by(HostSnapshot.captured_at.desc())
        .limit(limit)
        .all()
    )
    for snap in snapshots:
        older = (
            session.query(HostSnapshot)
            .filter(HostSnapshot.host_id == snap.host_id)
            .filter(HostSnapshot.captured_at < snap.captured_at)
            .order_by(HostSnapshot.captured_at.desc())
            .first()
        )
        feed.append(
            {
                "kind": "snapshot",
                "at": snap.captured_at,
                "hostname": snap.host.hostname if snap.host else "?",
                "changes": diff_snapshots(older.fields, snap.fields) if older else [],
                "initial": older is None,
            }
        )

    imports = (
        session.query(ImportLog)
        .order_by(ImportLog.imported_at.desc())
        .limit(limit)
        .all()
    )
    for log in imports:
        counts = {
            key: getattr(log, key)
            for key in _IMPORT_COUNTERS
            if getattr(log, key)  # skip zero and None
        }
        feed.append(
            {
                "kind": "import",
                "at": log.imported_at,
                "source": log.source.value,
                "filename": log.filename,
                "counts": counts,
            }
        )

    feed.sort(key=lambda e: e["at"], reverse=True)
    return feed[:limit]


def os_breakdown(session: Session) -> list[dict]:
    """[{family, count, pct}] sorted by count descending."""
    rows = (
        session.query(Host.os_family, func.count(Host.id))
        .group_by(Host.os_family)
        .all()
    )
    total = sum(count for _, count in rows)
    if not total:
        return []
    out = [
        {"family": family or "Unknown", "count": count, "pct": round(count * 100 / total)}
        for family, count in rows
    ]
    out.sort(key=lambda r: r["count"], reverse=True)
    return out
