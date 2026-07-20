"""Aggregations for the dashboard: freshness, change feed, OS mix.

The vulnerability trend lives in :mod:`cmdb.domain.services.vuln_snapshots`.

All functions take a Session and return plain dicts/lists; `now` is
injectable for tests. Datetimes are naive UTC, matching the models.
"""

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, HostSnapshot, ImportLog
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
