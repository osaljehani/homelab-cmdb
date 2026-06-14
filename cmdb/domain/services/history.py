from typing import Any

from sqlalchemy.orm import Session

from cmdb.domain.models import Host, HostSnapshot

# Fields that change on every import and would spam the change log.
VOLATILE_KEYS: set[str] = {"uptime_seconds"}


def _relevant(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop volatile keys so diffs only show meaningful changes."""
    return {k: v for k, v in fields.items() if k not in VOLATILE_KEYS and v is not None}


def record_snapshot(
    session: Session, host: Host, fields: dict[str, Any]
) -> HostSnapshot | None:
    """Insert a snapshot only if the meaningful fields changed since the last one.

    Returns the new snapshot, or None when nothing changed.
    """
    relevant = _relevant(fields)
    latest = (
        session.query(HostSnapshot)
        .filter_by(host_id=host.id)
        .order_by(HostSnapshot.captured_at.desc())
        .first()
    )
    if latest is not None and latest.fields == relevant:
        return None

    snapshot = HostSnapshot(host_id=host.id, fields=relevant)
    session.add(snapshot)
    session.flush()
    return snapshot


def diff_snapshots(
    old: dict[str, Any], new: dict[str, Any]
) -> list[tuple[str, Any, Any]]:
    """Return (field, old_value, new_value) for every field that differs."""
    changes: list[tuple[str, Any, Any]] = []
    for key in sorted(set(old) | set(new)):
        before = old.get(key)
        after = new.get(key)
        if before != after:
            changes.append((key, before, after))
    return changes


def host_history(session: Session, host: Host) -> list[dict[str, Any]]:
    """Newest-first timeline of changes for a host.

    Each entry: {captured_at, changes: [(field, old, new)], initial: bool}.
    The oldest snapshot is marked `initial` (the first time we saw the host).
    """
    snapshots = (
        session.query(HostSnapshot)
        .filter_by(host_id=host.id)
        .order_by(HostSnapshot.captured_at.desc())
        .all()
    )
    timeline: list[dict[str, Any]] = []
    for i, snap in enumerate(snapshots):
        older = snapshots[i + 1] if i + 1 < len(snapshots) else None
        if older is None:
            timeline.append(
                {"captured_at": snap.captured_at, "changes": [], "initial": True}
            )
        else:
            timeline.append(
                {
                    "captured_at": snap.captured_at,
                    "changes": diff_snapshots(older.fields, snap.fields),
                    "initial": False,
                }
            )
    return timeline
