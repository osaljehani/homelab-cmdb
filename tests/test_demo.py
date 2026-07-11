import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmdb.cli.main import app as cli_app
from cmdb.demo.seed import seed
from cmdb.domain.models import (
    Container,
    Host,
    HostSnapshot,
    Image,
    ImageScan,
    K8sCluster,
    Vulnerability,
)
from cmdb.domain.services.dashboard import fleet_freshness
from cmdb.web.app import app
from cmdb.web.deps import get_db_dep

runner = CliRunner()


def _client(db):
    app.dependency_overrides[get_db_dep] = lambda: db
    return TestClient(app)


class TestSeedData:
    def test_seeds_six_hosts(self, db):
        seed(db)
        assert db.query(Host).count() == 6

    def test_hostnames_are_fictional_example_lan(self, db):
        seed(db)
        hosts = db.query(Host).all()
        for h in hosts:
            assert h.fqdn is not None
            assert h.fqdn.endswith(".example.lan"), h.fqdn
            assert ".local" not in h.fqdn
            assert "192.168." not in (h.primary_ipv4 or "")

    def test_k8s_cluster_meridian_has_three_nodes(self, db):
        seed(db)
        cluster = db.query(K8sCluster).filter_by(name="meridian").first()
        assert cluster is not None
        assert len(cluster.nodes) == 3
        assert len(cluster.namespaces) == 4
        assert len(cluster.workloads) >= 4

    def test_containers_seeded(self, db):
        seed(db)
        count = db.query(Container).count()
        # atlas: nginx, postgres, grafana, prometheus (4); vega: vaultwarden, caddy (2)
        assert count >= 6

    def test_images_scans_and_vulnerabilities_seeded(self, db):
        seed(db)
        assert db.query(Image).count() > 0
        assert db.query(ImageScan).count() > 0
        assert db.query(Vulnerability).count() > 0

    def test_one_image_marked_expected_noisy(self, db):
        seed(db)
        noisy = db.query(Image).filter_by(expected_noisy=True).all()
        assert len(noisy) == 1

    def test_exactly_one_stale_host_rho(self, db):
        seed(db)
        now = datetime.utcnow()
        freshness = fleet_freshness(db, stale_days=7, now=now)
        assert freshness["stale"] == 1
        stale_hostnames = [h.hostname for h in freshness["stale_hosts"]]
        assert stale_hostnames == ["rho"]

    def test_snapshots_recorded_with_history(self, db):
        seed(db)
        hydra = db.query(Host).filter_by(hostname="hydra").first()
        assert hydra is not None
        snaps = (
            db.query(HostSnapshot)
            .filter_by(host_id=hydra.id)
            .order_by(HostSnapshot.captured_at)
            .all()
        )
        # run1 + run2 (a real diff was recorded since memory_mb changed)
        assert len(snaps) >= 2

    def test_rho_ip_changed_between_runs(self, db):
        seed(db)
        rho = db.query(Host).filter_by(hostname="rho").first()
        assert rho is not None
        assert rho.primary_ipv4 == "203.0.113.30"
        snaps = (
            db.query(HostSnapshot)
            .filter_by(host_id=rho.id)
            .order_by(HostSnapshot.captured_at)
            .all()
        )
        assert len(snaps) >= 2
        ips_seen = {s.fields.get("primary_ipv4") for s in snaps}
        assert "203.0.113.29" in ips_seen
        assert "203.0.113.30" in ips_seen

    def test_created_at_backdated(self, db):
        seed(db)
        now = datetime.utcnow()
        hosts = db.query(Host).all()
        for h in hosts:
            assert h.created_at <= now - timedelta(days=29)

    def test_tags_applied(self, db):
        seed(db)
        tag_names = set()
        for h in db.query(Host).all():
            tag_names.update(t.name for t in h.tags)
        assert {"docker", "prod", "k8s", "backup"} & tag_names

    def test_tailscale_and_ports_seeded(self, db):
        seed(db)
        atlas = db.query(Host).filter_by(hostname="atlas").first()
        assert atlas.tailscale_online is True
        assert atlas.tailscale_ipv4 is not None
        assert atlas.tailscale_ipv4.startswith("100.")
        assert len(atlas.listening_ports) > 0
        assert len(atlas.tailscale_services) > 0
        funnel_svc = [s for s in atlas.tailscale_services if s.funnel]
        assert len(funnel_svc) == 1

    def test_vega_is_exit_node(self, db):
        seed(db)
        vega = db.query(Host).filter_by(hostname="vega").first()
        assert vega.tailscale_exit_node is True
        assert vega.tailscale_online is True

class TestSeedWebPages:
    def test_dashboard_ok(self, db):
        seed(db)
        db.commit()
        client = _client(db)
        try:
            r = client.get("/")
            assert r.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_topology_data_ok(self, db):
        seed(db)
        db.commit()
        client = _client(db)
        try:
            r = client.get("/topology/data")
            assert r.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_network_ok(self, db):
        seed(db)
        db.commit()
        client = _client(db)
        try:
            r = client.get("/network/")
            assert r.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_images_ok(self, db):
        seed(db)
        db.commit()
        client = _client(db)
        try:
            r = client.get("/images/")
            assert r.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_host_detail_ok(self, db):
        seed(db)
        db.commit()
        client = _client(db)
        try:
            r = client.get("/hosts/atlas")
            assert r.status_code == 200
        finally:
            app.dependency_overrides.clear()


class TestDemoCommandDbSafety:
    """`cmdb demo --db <path>` wipe-safety behavior (CLI level, real subprocess).

    These invoke the actual `demo` command with --seed-only so the real
    demo-seed subprocess runs against a real tempfile DB — no mocking of the
    subprocess boundary, matching how `cmdb/cli/demo.py` actually works.
    """

    def test_user_supplied_existing_db_refused_in_fresh_mode(self, tmp_path):
        db_path = tmp_path / "existing.db"
        db_path.write_bytes(b"not a real sqlite db, just needs to exist")

        result = runner.invoke(cli_app, ["demo", "--seed-only", "--db", str(db_path)])

        assert result.exit_code == 1, result.output
        assert "refus" in result.output.lower()
        # Must NOT have been deleted or overwritten.
        assert db_path.read_bytes() == b"not a real sqlite db, just needs to exist"

    def test_user_supplied_nonexistent_db_seeds_fresh(self, tmp_path):
        db_path = tmp_path / "brand-new.db"
        assert not db_path.exists()

        result = runner.invoke(cli_app, ["demo", "--seed-only", "--db", str(db_path)])

        assert result.exit_code == 0, result.output
        assert db_path.exists()

    def test_keep_mode_reuses_already_seeded_db(self, tmp_path):
        db_path = tmp_path / "reuse.db"

        first = runner.invoke(cli_app, ["demo", "--seed-only", "--db", str(db_path)])
        assert first.exit_code == 0, first.output

        second = runner.invoke(
            cli_app, ["demo", "--seed-only", "--keep", "--db", str(db_path)]
        )

        assert second.exit_code == 0, second.output
        assert "reusing" in second.output.lower() or "already seeded" in second.output.lower()

    def test_default_tempdir_db_still_auto_wiped_when_fresh(self, monkeypatch):
        # The default (no --db) tempdir path stays throwaway: fresh mode wipes
        # it even though it may already exist from a prior run.
        fake_default = Path(tempfile.gettempdir()) / "cmdb-demo-test-autowipe.db"
        monkeypatch.setattr("cmdb.cli.demo._default_demo_db", lambda: fake_default)
        try:
            first = runner.invoke(cli_app, ["demo", "--seed-only"])
            assert first.exit_code == 0, first.output

            # Second fresh run must succeed too (auto-wiped, not refused).
            second = runner.invoke(cli_app, ["demo", "--seed-only"])
            assert second.exit_code == 0, second.output
            assert "refus" not in second.output.lower()
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(str(fake_default) + suffix if suffix else fake_default).unlink(
                    missing_ok=True
                )
