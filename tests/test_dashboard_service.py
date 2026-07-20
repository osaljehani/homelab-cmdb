from datetime import datetime, timedelta

from cmdb.domain.models import (
    Host,
    HostSnapshot,
    ImportLog,
    ImportSource,
)
from cmdb.domain.services.dashboard import (
    fleet_freshness,
    os_breakdown,
    recent_changes,
)

NOW = datetime(2026, 7, 1, 12, 0, 0)


def _host(i, **kw):
    return Host(machine_id=f"m{i:032d}", hostname=f"host-{i}", **kw)


class TestFleetFreshness:
    def test_buckets_and_tailscale_counts(self, db):
        never_seen = _host(3)
        db.add_all(
            [
                _host(1, last_seen=NOW - timedelta(days=1), tailscale_online=True),
                _host(2, last_seen=NOW - timedelta(days=30), tailscale_online=False),
                never_seen,
            ]
        )
        db.flush()
        never_seen.last_seen = None  # column default stamps on insert; force-null it
        db.commit()
        f = fleet_freshness(db, stale_days=7, now=NOW)
        assert f["total"] == 3
        assert f["fresh"] == 1
        assert f["stale"] == 1
        assert f["never"] == 1
        assert [h.hostname for h in f["stale_hosts"]] == ["host-2"]
        assert f["ts_online"] == 1
        assert f["ts_offline"] == 1
        assert f["ts_absent"] == 1

    def test_exactly_stale_days_old_is_stale(self, db):
        db.add(_host(1, last_seen=NOW - timedelta(days=7)))
        db.commit()
        f = fleet_freshness(db, stale_days=7, now=NOW)
        assert f["stale"] == 1 and f["fresh"] == 0

    def test_empty_db(self, db):
        f = fleet_freshness(db, now=NOW)
        assert f["total"] == 0 and f["stale_hosts"] == []


class TestRecentChanges:
    def test_merges_snapshots_and_imports_desc(self, db):
        host = _host(1)
        db.add(host)
        db.flush()
        db.add(
            HostSnapshot(
                host_id=host.id,
                captured_at=NOW - timedelta(hours=3),
                fields={"kernel": "6.1.0"},
            )
        )
        db.add(
            HostSnapshot(
                host_id=host.id,
                captured_at=NOW - timedelta(hours=1),
                fields={"kernel": "6.2.0"},
            )
        )
        db.add(
            ImportLog(
                imported_at=NOW - timedelta(hours=2),
                source=ImportSource.WEB,
                filename="facts.json",
                hosts_upserted=3,
            )
        )
        db.commit()
        feed = recent_changes(db)
        assert [e["kind"] for e in feed] == ["snapshot", "import", "snapshot"]
        # newest snapshot diffs against the older one
        assert feed[0]["hostname"] == "host-1"
        assert ("kernel", "6.1.0", "6.2.0") in feed[0]["changes"]
        # oldest snapshot is the host's first sighting
        assert feed[2]["initial"] is True
        assert feed[1]["counts"] == {"hosts_upserted": 3}

    def test_limit(self, db):
        for i in range(15):
            db.add(
                ImportLog(
                    imported_at=NOW - timedelta(minutes=i),
                    source=ImportSource.CLI,
                    hosts_upserted=1,
                )
            )
        db.commit()
        assert len(recent_changes(db, limit=10)) == 10


class TestOsBreakdown:
    def test_counts_and_pct(self, db):
        db.add_all(
            [
                _host(1, os_family="Debian"),
                _host(2, os_family="Debian"),
                _host(3, os_family="RedHat"),
                _host(4, os_family=None),
            ]
        )
        db.commit()
        rows = os_breakdown(db)
        assert rows[0] == {"family": "Debian", "count": 2, "pct": 50}
        assert {"family": "Unknown", "count": 1, "pct": 25} in rows

    def test_empty_db(self, db):
        assert os_breakdown(db) == []
