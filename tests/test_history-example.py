from sqlalchemy.orm import Session

from cmdb.domain.models import HostSnapshot
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.history import diff_snapshots, host_history
from cmdb.domain.services.hosts import get_host


def _count(db: Session) -> int:
    return db.query(HostSnapshot).count()


def test_first_import_creates_snapshot(db: Session, host_facts: dict):
    import_host(db, host_facts)
    assert _count(db) == 1


def test_unchanged_reimport_no_new_snapshot(db: Session, host_facts: dict):
    import_host(db, host_facts)
    import_host(db, host_facts)
    assert _count(db) == 1


def test_volatile_only_change_no_new_snapshot(db: Session, host_facts: dict):
    import_host(db, host_facts)
    # uptime changes every import but is volatile   must not create a snapshot.
    host_facts["ansible_facts"]["ansible_uptime_seconds"] = 999999
    import_host(db, host_facts)
    assert _count(db) == 1


def test_meaningful_change_creates_snapshot_and_diff(db: Session, host_facts: dict):
    import_host(db, host_facts)
    host_facts["ansible_facts"]["ansible_kernel"] = "6.1.0-new-generic"
    import_host(db, host_facts)
    db.commit()
    assert _count(db) == 2

    host = get_host(db, "testhost")
    timeline = host_history(db, host)
    # newest-first: [changed, initial]
    assert len(timeline) == 2
    assert timeline[0]["initial"] is False
    assert timeline[1]["initial"] is True

    changes = dict((f, (o, n)) for f, o, n in timeline[0]["changes"])
    assert "kernel" in changes
    assert changes["kernel"] == ("5.15.0-91-generic", "6.1.0-new-generic")


def test_diff_snapshots_detects_added_and_changed():
    old = {"a": 1, "b": 2}
    new = {"a": 1, "b": 3, "c": 4}
    changes = diff_snapshots(old, new)
    assert ("b", 2, 3) in changes
    assert ("c", None, 4) in changes
    assert all(f != "a" for f, _, _ in changes)
