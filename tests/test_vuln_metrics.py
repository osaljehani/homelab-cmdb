"""Dashboard CVE metrics: trend deltas, unique CVE counts, worst package per host.

Synthetic fixtures only — fictional hosts, clusters and image refs.
"""

from datetime import date, datetime

from cmdb.domain.models import (
    Container,
    Host,
    Image,
    ImageScan,
    K8sCluster,
    K8sWorkload,
    Vulnerability,
    VulnSnapshot,
)
from cmdb.domain.services.vuln_metrics import (
    top_risk_packages,
    trend_deltas,
    unique_cves_by_severity,
)

NOW = datetime(2026, 7, 1, 12, 0)


def _snapshot(db, day, total, ref="app:1"):
    db.add(
        VulnSnapshot(
            snapshot_date=day,
            image_ref=ref,
            was_running=True,
            was_noisy=False,
            scanned_at=datetime.combine(day, datetime.min.time()),
            total=total,
        )
    )
    db.flush()


def _image(db, ref, vulns, scanned_at=NOW, noisy=False):
    """An image with one scan holding ``vulns`` = [(cve, severity, pkg), ...]."""
    img = db.query(Image).filter(Image.ref == ref).first()
    if img is None:
        img = Image(ref=ref, first_seen=scanned_at, expected_noisy=noisy)
        db.add(img)
        db.flush()
    scan = ImageScan(image_id=img.id, scanned_at=scanned_at, source="docker")
    db.add(scan)
    db.flush()
    for cve, sev, pkg in vulns:
        db.add(Vulnerability(scan_id=scan.id, vuln_id=cve, severity=sev, pkg_name=pkg))
    db.flush()
    return img


def _docker(db, hostname, ref):
    host = db.query(Host).filter(Host.hostname == hostname).first()
    if host is None:
        host = Host(machine_id=f"mid-{hostname}", hostname=hostname)
        db.add(host)
        db.flush()
    db.add(Container(host_id=host.id, name=ref.split(":")[0], image=ref, state="running"))
    db.flush()


def _k8s(db, cluster_name, ref):
    cluster = db.query(K8sCluster).filter(K8sCluster.name == cluster_name).first()
    if cluster is None:
        cluster = K8sCluster(name=cluster_name)
        db.add(cluster)
        db.flush()
    db.add(
        K8sWorkload(
            cluster_id=cluster.id,
            namespace="apps",
            pod_name=f"{ref.split(':')[0]}-abc",
            container_name="main",
            image=ref,
            image_canonical=ref,
        )
    )
    db.flush()


class TestTrendDeltas:
    def test_up(self, db):
        _snapshot(db, date(2026, 6, 1), 100)
        _snapshot(db, date(2026, 6, 30), 112)
        (d30,) = trend_deltas(db, windows=(30,), now=NOW)
        assert d30["days"] == 30
        assert d30["start_date"] == date(2026, 6, 1)
        assert (d30["start_total"], d30["end_total"]) == (100, 112)
        assert d30["delta"] == 12
        assert d30["pct"] == 12.0

    def test_down(self, db):
        _snapshot(db, date(2026, 6, 1), 200)
        _snapshot(db, date(2026, 7, 1), 150)
        (d30,) = trend_deltas(db, windows=(30,), now=NOW)
        assert d30["delta"] == -50
        assert d30["pct"] == -25.0

    def test_window_bounds_start_point(self, db):
        _snapshot(db, date(2026, 4, 5), 50)  # inside 90d, outside 30d
        _snapshot(db, date(2026, 6, 15), 80)
        _snapshot(db, date(2026, 7, 1), 100)
        d30, d90 = trend_deltas(db, windows=(30, 90), now=NOW)
        assert d30["start_total"] == 80
        assert d90["start_total"] == 50
        assert d90["pct"] == 100.0

    def test_zero_start_has_no_pct(self, db):
        _snapshot(db, date(2026, 6, 10), 0)
        _snapshot(db, date(2026, 7, 1), 10)
        (d30,) = trend_deltas(db, windows=(30,), now=NOW)
        assert d30["delta"] == 10
        assert d30["pct"] is None

    def test_single_point_has_no_pct(self, db):
        _snapshot(db, date(2026, 7, 1), 10)
        (d30,) = trend_deltas(db, windows=(30,), now=NOW)
        assert d30["pct"] is None
        assert d30["delta"] is None

    def test_empty(self, db):
        d30, d90 = trend_deltas(db, now=NOW)
        assert d30["pct"] is None and d90["pct"] is None
        assert (d30["days"], d90["days"]) == (30, 90)


class TestUniqueCves:
    def test_dedupes_across_images(self, db):
        _image(db, "web:1", [("CVE-A", "CRITICAL", "libc"), ("CVE-B", "LOW", "zlib")])
        _image(db, "api:1", [("CVE-A", "CRITICAL", "libc"), ("CVE-C", "HIGH", "openssl")])
        _docker(db, "node-a.example.lan", "web:1")
        _docker(db, "node-b.example.lan", "api:1")
        u = unique_cves_by_severity(db)
        assert u == {"critical": 1, "high": 1, "medium": 0, "low": 1, "unknown": 0, "total": 3}

    def test_highest_severity_wins(self, db):
        _image(db, "web:1", [("CVE-A", "MEDIUM", "libc")])
        _image(db, "api:1", [("CVE-A", "HIGH", "libc")])
        _docker(db, "node-a.example.lan", "web:1")
        _docker(db, "node-a.example.lan", "api:1")
        u = unique_cves_by_severity(db)
        assert (u["high"], u["medium"], u["total"]) == (1, 0, 1)

    def test_only_latest_scan_running_non_noisy(self, db):
        _image(db, "web:1", [("CVE-OLD", "CRITICAL", "libc")], scanned_at=datetime(2026, 6, 1))
        _image(db, "web:1", [("CVE-NEW", "HIGH", "libc")])
        _image(db, "noisy:1", [("CVE-N", "CRITICAL", "x")], noisy=True)
        _image(db, "regonly:1", [("CVE-R", "CRITICAL", "y")])
        _docker(db, "node-a.example.lan", "web:1")
        _docker(db, "node-a.example.lan", "noisy:1")
        u = unique_cves_by_severity(db)
        assert (u["critical"], u["high"], u["total"]) == (0, 1, 1)

    def test_empty(self, db):
        assert unique_cves_by_severity(db)["total"] == 0


class TestTopRiskPackages:
    def test_ranks_crit_before_high_per_host(self, db):
        _image(
            db,
            "web:1",
            [
                ("CVE-1", "HIGH", "openssl"),
                ("CVE-2", "HIGH", "openssl"),
                ("CVE-3", "HIGH", "openssl"),
                ("CVE-4", "CRITICAL", "libc"),
                ("CVE-5", "MEDIUM", "zlib"),
            ],
        )
        _docker(db, "node-a.example.lan", "web:1")
        (row,) = top_risk_packages(db)
        assert row == {
            "host": "node-a.example.lan",
            "kind": "docker",
            "package": "libc",
            "crit": 1,
            "high": 0,
            "images": ["web:1"],
        }

    def test_groups_by_host_and_cluster_and_sorts(self, db):
        _image(db, "web:1", [("CVE-1", "HIGH", "openssl")])
        _image(db, "api:1", [("CVE-1", "HIGH", "openssl"), ("CVE-2", "HIGH", "openssl")])
        _image(db, "svc:1", [("CVE-9", "CRITICAL", "glibc")])
        _docker(db, "node-a.example.lan", "web:1")
        _docker(db, "node-a.example.lan", "api:1")
        _k8s(db, "lab-cluster", "svc:1")
        rows = top_risk_packages(db)
        assert [(r["host"], r["kind"]) for r in rows] == [
            ("lab-cluster", "k8s"),
            ("node-a.example.lan", "docker"),
        ]
        docker_row = rows[1]
        assert docker_row["package"] == "openssl"
        assert (docker_row["crit"], docker_row["high"]) == (0, 2)  # distinct CVEs
        assert docker_row["images"] == ["api:1", "web:1"]

    def test_excludes_noisy_registry_only_and_clean_hosts(self, db):
        _image(db, "noisy:1", [("CVE-1", "CRITICAL", "libc")], noisy=True)
        _image(db, "regonly:1", [("CVE-2", "CRITICAL", "libc")])
        _image(db, "clean:1", [("CVE-3", "LOW", "zlib")])
        _docker(db, "node-a.example.lan", "noisy:1")
        _docker(db, "node-b.example.lan", "clean:1")
        assert top_risk_packages(db) == []

    def test_package_name_breaks_ties(self, db):
        _image(db, "web:1", [("CVE-1", "HIGH", "zlib"), ("CVE-2", "HIGH", "bash")])
        _docker(db, "node-a.example.lan", "web:1")
        (row,) = top_risk_packages(db)
        assert row["package"] == "bash"

    def test_cve_both_crit_and_high_counts_once_as_crit(self, db):
        _image(db, "web:1", [("CVE-1", "CRITICAL", "libc")])
        _image(db, "api:1", [("CVE-1", "HIGH", "libc")])
        _docker(db, "node-a.example.lan", "web:1")
        _docker(db, "node-a.example.lan", "api:1")
        (row,) = top_risk_packages(db)
        assert (row["crit"], row["high"]) == (1, 0)
