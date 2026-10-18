from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from cmdb.domain.models import (
    Container,
    Host,
    Image,
    ImageScan,
    K8sCluster,
    K8sWorkload,
    VulnSnapshot,
)
from cmdb.domain.services import images as images_svc
from cmdb.domain.services.vuln_snapshots import (
    backfill_snapshots,
    snapshot_trend,
    write_daily_snapshot,
)

NOW = datetime(2026, 7, 1, 12, 0, 0)


def _img(db, ref, noisy=False, scans=()):
    img = Image(ref=ref, expected_noisy=noisy, first_seen=NOW)
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


def _run_container(db, image_ref, name="c1"):
    host = db.query(Host).first()
    if host is None:
        host = Host(machine_id="m" + name, hostname="testhost")
        db.add(host)
        db.flush()
    db.add(Container(host_id=host.id, name=name, image=image_ref, state="running"))
    db.flush()


def _run_k8s(db, image_canonical, pod="pod-1"):
    cluster = db.query(K8sCluster).first()
    if cluster is None:
        cluster = K8sCluster(name="demo-cluster")
        db.add(cluster)
        db.flush()
    db.add(
        K8sWorkload(
            cluster_id=cluster.id,
            namespace="default",
            pod_name=pod,
            container_name="main",
            image=image_canonical,
            image_canonical=image_canonical,
        )
    )
    db.flush()


class TestWriteDailySnapshot:
    def test_writes_row_per_scanned_image_with_flags(self, db: Session):
        _img(db, "gitea/gitea:latest", scans=[(NOW - timedelta(hours=1), 2, 3)])
        _run_container(db, "gitea/gitea:latest")
        _img(db, "portfolio:0.0.1", scans=[(NOW - timedelta(hours=1), 1, 0)])
        _run_k8s(db, "portfolio:0.0.1")
        _img(db, "registry-only:1", scans=[(NOW - timedelta(hours=1), 4, 0)])
        _img(db, "noisy:1", noisy=True, scans=[(NOW - timedelta(hours=1), 9, 0)])
        _img(db, "unscanned:1")

        written = write_daily_snapshot(db, now=NOW)

        assert written == 4  # unscanned image skipped
        rows = {s.image_ref: s for s in db.query(VulnSnapshot).all()}
        assert set(rows) == {
            "gitea/gitea:latest",
            "portfolio:0.0.1",
            "registry-only:1",
            "noisy:1",
        }
        assert all(s.snapshot_date == NOW.date() for s in rows.values())
        assert rows["gitea/gitea:latest"].was_running is True
        assert rows["gitea/gitea:latest"].critical == 2
        assert rows["gitea/gitea:latest"].total == 5
        assert rows["portfolio:0.0.1"].was_running is True  # k8s placement
        assert rows["registry-only:1"].was_running is False
        assert rows["noisy:1"].was_noisy is True

    def test_uses_latest_scan_per_image(self, db: Session):
        _img(
            db,
            "app:1",
            scans=[(NOW - timedelta(days=3), 9, 0), (NOW - timedelta(hours=1), 1, 0)],
        )
        write_daily_snapshot(db, now=NOW)
        row = db.query(VulnSnapshot).one()
        assert row.critical == 1
        assert row.scanned_at == NOW - timedelta(hours=1)

    def test_same_day_rerun_replaces_rows(self, db: Session):
        img = _img(db, "app:1", scans=[(NOW - timedelta(hours=2), 5, 0)])
        write_daily_snapshot(db, now=NOW)
        db.add(
            ImageScan(
                image_id=img.id,
                scanned_at=NOW - timedelta(hours=1),
                critical=2,
                total=2,
            )
        )
        db.flush()
        write_daily_snapshot(db, now=NOW)
        row = db.query(VulnSnapshot).one()  # one row, not two
        assert row.critical == 2

    def test_other_days_untouched(self, db: Session):
        _img(db, "app:1", scans=[(NOW - timedelta(days=1, hours=1), 5, 0)])
        write_daily_snapshot(db, now=NOW - timedelta(days=1))
        write_daily_snapshot(db, now=NOW)
        assert db.query(VulnSnapshot).count() == 2


class TestSnapshotTrend:
    def _snap(self, db, day, ref="app:1", running=True, noisy=False, crit=1, total=1):
        db.add(
            VulnSnapshot(
                snapshot_date=day,
                image_ref=ref,
                was_running=running,
                was_noisy=noisy,
                critical=crit,
                high=0,
                medium=0,
                low=0,
                unknown=0,
                total=total,
            )
        )
        db.flush()

    def test_sums_running_non_noisy_per_day(self, db: Session):
        day = (NOW - timedelta(days=2)).date()
        self._snap(db, day, "a:1", crit=2, total=5)
        self._snap(db, day, "b:1", crit=1, total=3)
        self._snap(db, day, "registry:1", running=False, crit=7, total=7)
        self._snap(db, day, "noisy:1", noisy=True, crit=9, total=9)

        points = snapshot_trend(db, days=30, now=NOW)

        assert points == [
            {
                "date": day,
                "critical": 3,
                "high": 0,
                "medium": 0,
                "low": 0,
                "unknown": 0,
                "total": 8,
            }
        ]

    def test_past_points_keep_deleted_image_contribution(self, db: Session):
        # The remediation scenario: an image contributes yesterday, is deleted
        # today — yesterday's point survives, today's point drops.
        yesterday = NOW - timedelta(days=1)
        _img(db, "vulnerable:1", scans=[(yesterday - timedelta(hours=1), 200, 0)])
        _img(db, "healthy:1", scans=[(yesterday - timedelta(hours=1), 1, 0)])
        _run_container(db, "vulnerable:1", name="vuln-c")
        _run_container(db, "healthy:1", name="ok-c")
        write_daily_snapshot(db, now=yesterday)

        images_svc.delete_image(db, "vulnerable:1")  # refreshes *real* today

        real_now = datetime.utcnow()
        # NOW is fixed but delete_image refreshes the real today, so the window
        # must reach back to the fixture date or the test rots once NOW ages out.
        days = (real_now.date() - yesterday.date()).days + 1
        points = snapshot_trend(db, days=days, now=real_now)
        by_date = {p["date"]: p for p in points}
        assert by_date[yesterday.date()]["critical"] == 201  # history intact
        assert by_date[real_now.date()]["critical"] == 1  # drop visible today

    def test_window_excludes_old_days(self, db: Session):
        self._snap(db, (NOW - timedelta(days=60)).date())
        assert snapshot_trend(db, days=30, now=NOW) == []

    def test_empty_db(self, db: Session):
        assert snapshot_trend(db, now=NOW) == []


class TestSetNoisySnapshot:
    def test_set_noisy_refreshes_todays_flag(self, db: Session):
        real_now = datetime.utcnow()
        _img(db, "chatty:1", scans=[(real_now - timedelta(hours=1), 3, 0)])
        _run_container(db, "chatty:1")
        write_daily_snapshot(db)
        assert db.query(VulnSnapshot).one().was_noisy is False

        images_svc.set_noisy(db, "chatty:1", True)

        assert db.query(VulnSnapshot).one().was_noisy is True
        assert snapshot_trend(db, now=real_now) == []


class TestBackfill:
    def test_carry_forward_latest_scan_per_image(self, db: Session):
        # Ported from the old TestVulnTrend.test_daily_points_use_latest_scan:
        # the later day reflects only the newest scan, not the sum of both.
        _img(
            db,
            "app:1",
            scans=[(NOW - timedelta(days=10), 5, 2), (NOW - timedelta(days=2), 1, 0)],
        )
        _run_container(db, "app:1")

        written = backfill_snapshots(db, now=NOW)

        assert written == 2
        rows = {s.snapshot_date: s for s in db.query(VulnSnapshot).all()}
        assert rows[(NOW - timedelta(days=10)).date()].critical == 5
        assert rows[(NOW - timedelta(days=2)).date()].critical == 1
        assert all(r.was_running for r in rows.values())

    def test_idempotent_and_preserves_existing_days(self, db: Session):
        _img(db, "app:1", scans=[(NOW - timedelta(days=2), 1, 0)])
        write_daily_snapshot(db, now=NOW - timedelta(days=2))
        assert backfill_snapshots(db, now=NOW) == 0  # day already covered
        assert db.query(VulnSnapshot).count() == 1

    def test_empty_db_is_noop(self, db: Session):
        assert backfill_snapshots(db, now=NOW) == 0


class TestInventoryImportHooks:
    """Docker/K8s inventory imports refresh today's snapshot, so the trend
    reacts to a remediation (stopped/replaced container) at the next /collect
    or inventory upload — not only at the next scan import."""

    def test_docker_import_refresh_drops_stopped_container(self, db: Session, tmp_path):
        import json

        from cmdb.domain.models import ImportSource
        from cmdb.domain.services.docker_import import import_from_path

        real_now = datetime.utcnow()
        _img(db, "app:1", scans=[(real_now - timedelta(hours=1), 3, 0)])
        _run_container(db, "app:1", name="app")
        write_daily_snapshot(db)
        assert db.query(VulnSnapshot).one().was_running is True

        # Next collection: the container is now exited (remediated/stopped).
        f = tmp_path / "docker.json"
        f.write_text(
            json.dumps(
                {
                    "host": "testhost",
                    "containers": [
                        {"name": "app", "image": "app:1", "state": "exited"}
                    ],
                }
            )
        )
        import_from_path(db, str(f), ImportSource.CLI)

        assert db.query(VulnSnapshot).one().was_running is False
        assert snapshot_trend(db, now=real_now) == []

    def test_k8s_import_refresh_marks_new_workload_running(self, db: Session, tmp_path):
        import json

        from cmdb.domain.models import ImportSource
        from cmdb.domain.services.k8s_import import import_from_path

        real_now = datetime.utcnow()
        _img(db, "portfolio:0.0.1", scans=[(real_now - timedelta(hours=1), 1, 0)])
        write_daily_snapshot(db)
        assert db.query(VulnSnapshot).one().was_running is False

        f = tmp_path / "k8s.json"
        f.write_text(
            json.dumps(
                {
                    "cluster": "demo-cluster",
                    "nodes": [],
                    "namespaces": [],
                    "workloads": [
                        {
                            "namespace": "default",
                            "pod_name": "portfolio-abc",
                            "container_name": "main",
                            "image": "portfolio:0.0.1",
                        }
                    ],
                }
            )
        )
        import_from_path(db, str(f), ImportSource.CLI)

        assert db.query(VulnSnapshot).one().was_running is True
