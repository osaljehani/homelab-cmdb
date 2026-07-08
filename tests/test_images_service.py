from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import Container, Host, Image, ImageScan, Vulnerability
from cmdb.domain.services import images as images_svc


def _img(db, ref, noisy=False, scans=()):
    img = Image(ref=ref, expected_noisy=noisy, first_seen=datetime.utcnow())
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


def test_list_images_excludes_noisy_when_requested(db: Session):
    _img(db, "nginx:latest")
    _img(db, "noisy-tool:latest", noisy=True)
    assert {i.ref for i in images_svc.list_images(db)} == {
        "nginx:latest",
        "noisy-tool:latest",
    }
    assert {i.ref for i in images_svc.list_images(db, include_noisy=False)} == {
        "nginx:latest"
    }


def test_latest_scan_picks_most_recent(db: Session):
    img = _img(
        db,
        "nginx:latest",
        scans=[
            (datetime(2026, 7, 1), 1, 0),
            (datetime(2026, 7, 3), 5, 2),
        ],
    )
    latest = images_svc.latest_scan(db, img)
    assert latest.scanned_at == datetime(2026, 7, 3)
    assert latest.critical == 5


def test_set_noisy_toggles_and_raises_on_missing(db: Session):
    _img(db, "noisy-tool:latest")
    img = images_svc.set_noisy(db, "noisy-tool:latest", True)
    assert img.expected_noisy is True
    with pytest.raises(ValueError, match="not found"):
        images_svc.set_noisy(db, "ghost:1", True)


def test_delete_image_removes_image_and_scan_history(db: Session):
    img = _img(
        db, "gone:1", scans=[(datetime(2026, 7, 1), 1, 1), (datetime(2026, 7, 3), 2, 3)]
    )
    db.add(Vulnerability(scan_id=img.scans[0].id, vuln_id="CVE-1", severity="HIGH"))
    db.flush()

    result = images_svc.delete_image(db, "gone:1")

    assert result == {"ref": "gone:1", "scans": 2, "vulnerabilities": 1}
    assert images_svc.get_image(db, "gone:1") is None
    assert db.query(ImageScan).filter_by(image_id=img.id).count() == 0
    assert db.query(Vulnerability).count() == 0


def test_delete_image_raises_on_missing(db: Session):
    with pytest.raises(ValueError, match="not found"):
        images_svc.delete_image(db, "ghost:1")


def test_source_watermarks_groups_by_source_with_null_bucket(db: Session):
    a = _img(db, "a:1")
    _scan_with_source(db, a, "docker", datetime(2026, 7, 1))
    _scan_with_source(db, a, "docker", datetime(2026, 7, 2))
    b = _img(db, "b:1")
    _scan_with_source(db, b, "kubernetes", datetime(2026, 7, 5))
    c = _img(db, "c:1")
    _scan_with_source(db, c, None, datetime(2026, 7, 3))
    assert images_svc.source_watermarks(db) == {
        "docker": datetime(2026, 7, 2),
        "kubernetes": datetime(2026, 7, 5),
        None: datetime(2026, 7, 3),
    }


def test_source_watermarks_empty_when_no_scans(db: Session):
    _img(db, "unscanned:1")
    assert images_svc.source_watermarks(db) == {}


def test_is_stale_true_when_image_missed_latest_run_of_its_only_source():
    latest = {"docker": datetime(2026, 7, 1)}
    marks = {"docker": datetime(2026, 7, 5)}
    assert images_svc.is_stale(latest, marks) is True


def test_is_stale_false_when_newer_run_is_from_other_source():
    # The bug this feature fixes: a registry (kubernetes) scan run must not
    # flip docker-scanned images stale.
    latest = {"docker": datetime(2026, 7, 3)}
    marks = {"docker": datetime(2026, 7, 3), "kubernetes": datetime(2026, 7, 5)}
    assert images_svc.is_stale(latest, marks) is False


def test_is_stale_current_if_any_source_matches_its_watermark():
    latest = {"docker": datetime(2026, 7, 3), "kubernetes": datetime(2026, 7, 1)}
    marks = {"docker": datetime(2026, 7, 3), "kubernetes": datetime(2026, 7, 5)}
    assert images_svc.is_stale(latest, marks) is False


def test_is_stale_true_only_when_all_sources_missed():
    latest = {"docker": datetime(2026, 7, 1), "kubernetes": datetime(2026, 7, 2)}
    marks = {"docker": datetime(2026, 7, 3), "kubernetes": datetime(2026, 7, 5)}
    assert images_svc.is_stale(latest, marks) is True


def test_is_stale_false_for_never_scanned_image():
    assert images_svc.is_stale({}, {"docker": datetime(2026, 7, 5)}) is False


def test_null_source_scans_compare_within_null_bucket():
    latest = {None: datetime(2026, 7, 1)}
    marks = {None: datetime(2026, 7, 3), "docker": datetime(2026, 7, 5)}
    assert images_svc.is_stale(latest, marks) is True
    current = {None: datetime(2026, 7, 3)}
    assert images_svc.is_stale(current, marks) is False


def _host(db, machine_id, hostname):
    h = Host(machine_id=machine_id, hostname=hostname)
    db.add(h)
    db.flush()
    return h


def _container(db, host, name, image, state=None):
    db.add(Container(host_id=host.id, name=name, image=image, state=state))
    db.flush()


def _scan_with_source(db, img, source, when=datetime(2026, 7, 3)):
    db.add(ImageScan(image_id=img.id, scanned_at=when, source=source, total=0))
    img.last_scanned_at = when
    db.flush()


def test_deployments_docker_match_returns_host_and_container(db: Session):
    host = _host(db, "m1", "testhost")
    img = _img(db, "gitea/gitea:latest")
    _scan_with_source(db, img, "docker")
    _container(db, host, "gitea", "gitea/gitea:latest")
    dep = images_svc.deployments(db, img)
    assert dep["docker"] == [{"host": "testhost", "name": "gitea"}]
    assert dep["source"] == "docker"


def test_deployments_kubernetes_image_has_no_docker_but_source_tag(db: Session):
    img = _img(db, "portfolio:0.0.1")
    _scan_with_source(db, img, "kubernetes")
    dep = images_svc.deployments(db, img)
    assert dep["docker"] == []
    assert dep["source"] == "kubernetes"


def test_deployments_multiple_containers_same_image(db: Session):
    host = _host(db, "m1", "testhost")
    img = _img(db, "redis:7")
    _scan_with_source(db, img, "docker")
    _container(db, host, "redis-a", "redis:7")
    _container(db, host, "redis-b", "redis:7")
    dep = images_svc.deployments(db, img)
    assert {p["name"] for p in dep["docker"]} == {"redis-a", "redis-b"}


def test_deployments_unscanned_image_has_no_source(db: Session):
    img = _img(db, "unscanned:1")
    assert images_svc.deployments(db, img) == {
        "docker": [],
        "source": None,
        "status": "registry-only",
    }


def test_deployments_status_running_when_container_matches(db: Session):
    host = _host(db, "m1", "testhost")
    img = _img(db, "gitea/gitea:latest")
    _scan_with_source(db, img, "docker")
    _container(db, host, "gitea", "gitea/gitea:latest", state="running")
    assert images_svc.deployments(db, img)["status"] == "running"


def test_deployments_status_registry_only_without_container(db: Session):
    img = _img(db, "portfolio:0.0.1")
    _scan_with_source(db, img, "kubernetes")
    assert images_svc.deployments(db, img)["status"] == "registry-only"


def test_deployments_ignores_exited_containers(db: Session):
    host = _host(db, "m1", "testhost")
    img = _img(db, "redis:7")
    _scan_with_source(db, img, "docker")
    _container(db, host, "redis-old", "redis:7", state="exited")
    dep = images_svc.deployments(db, img)
    assert dep["docker"] == []
    assert dep["status"] == "registry-only"


def test_deployments_counts_stateless_container_as_running(db: Session):
    # Pre-state-column imports lack state; benefit of the doubt, same as the
    # containers page convention.
    host = _host(db, "m1", "testhost")
    img = _img(db, "redis:7")
    _scan_with_source(db, img, "docker")
    _container(db, host, "redis-a", "redis:7")  # state=None
    assert images_svc.deployments(db, img)["status"] == "running"


def test_image_overview_orders_running_first_then_critical_noisy_last(db: Session):
    host = _host(db, "m1", "testhost")
    _img(db, "noisy-tool:latest", noisy=True, scans=[(datetime(2026, 7, 3), 99, 0)])
    _img(db, "portfolio:0.0.1", scans=[(datetime(2026, 7, 3), 5, 0)])
    _img(db, "redis:7", scans=[(datetime(2026, 7, 3), 1, 0)])
    _img(db, "gitea/gitea:latest", scans=[(datetime(2026, 7, 3), 3, 0)])
    _container(db, host, "redis-a", "redis:7", state="running")
    _container(db, host, "gitea", "gitea/gitea:latest", state="running")
    rows = images_svc.image_overview(db)
    assert [r["image"].ref for r in rows] == [
        "gitea/gitea:latest",  # running, crit 3
        "redis:7",  # running, crit 1
        "portfolio:0.0.1",  # registry-only, crit 5
        "noisy-tool:latest",  # noisy last despite crit 99
    ]


def test_image_overview_row_shape_and_batched_stale(db: Session):
    host = _host(db, "m1", "testhost")
    docker_img = _img(db, "gitea/gitea:latest")
    _scan_with_source(db, docker_img, "docker", datetime(2026, 7, 3))
    _container(db, host, "gitea", "gitea/gitea:latest", state="running")
    dropped = _img(db, "old:1")
    _scan_with_source(db, dropped, "docker", datetime(2026, 7, 1))
    reg = _img(db, "portfolio:0.0.1")
    _scan_with_source(db, reg, "kubernetes", datetime(2026, 7, 5))

    rows = {r["image"].ref: r for r in images_svc.image_overview(db)}
    g = rows["gitea/gitea:latest"]
    assert g["status"] == "running"
    assert g["stale"] is False  # newer kubernetes run must not flip it
    assert g["scan"].scanned_at == datetime(2026, 7, 3)
    assert g["deployments"]["docker"] == [{"host": "testhost", "name": "gitea"}]
    assert g["latest_by_source"] == {"docker": datetime(2026, 7, 3)}
    assert rows["old:1"]["stale"] is True  # missed the newest docker run
    assert rows["portfolio:0.0.1"]["status"] == "registry-only"
    assert rows["portfolio:0.0.1"]["stale"] is False


def test_image_status_matches_overview_row(db: Session):
    host = _host(db, "m1", "testhost")
    img = _img(db, "gitea/gitea:latest")
    _scan_with_source(db, img, "docker")
    _container(db, host, "gitea", "gitea/gitea:latest", state="running")
    status = images_svc.image_status(db, img)
    row = next(
        r
        for r in images_svc.image_overview(db)
        if r["image"].ref == "gitea/gitea:latest"
    )
    assert status["status"] == row["status"] == "running"
    assert status["stale"] == row["stale"] is False
    assert status["scan"].id == row["scan"].id
    assert status["deployments"] == row["deployments"]


def test_deployments_matches_tagless_container_to_latest_image(db: Session):
    host = _host(db, "m1", "testhost")
    img = _img(db, "homelabcmdb-cmdb:latest")  # canonical Image.ref
    _scan_with_source(db, img, "docker")
    _container(
        db, host, "homelabcmdb-cmdb-1", "homelabcmdb-cmdb"
    )  # docker ps is tagless
    dep = images_svc.deployments(db, img)
    assert dep["docker"] == [{"host": "testhost", "name": "homelabcmdb-cmdb-1"}]


def test_vuln_summary_excludes_noisy_and_uses_latest(db: Session):
    _img(
        db,
        "nginx:latest",
        scans=[(datetime(2026, 7, 1), 1, 1), (datetime(2026, 7, 3), 2, 3)],
    )
    _img(db, "noisy-tool:latest", noisy=True, scans=[(datetime(2026, 7, 3), 99, 99)])
    _img(db, "unscanned:1")  # no scans
    s = images_svc.vuln_summary(db)
    assert s["images"] == 2  # non-noisy images
    assert s["scanned_images"] == 1  # only nginx has a scan
    assert s["critical"] == 2 and s["high"] == 3 and s["total"] == 5


def test_vuln_summary_buckets_running_and_registry_only(db: Session):
    host = _host(db, "m1", "testhost")
    _img(db, "gitea/gitea:latest", scans=[(datetime(2026, 7, 3), 2, 1)])
    _container(db, host, "gitea", "gitea/gitea:latest", state="running")
    _img(db, "portfolio:0.0.1", scans=[(datetime(2026, 7, 3), 1, 4)])
    _img(db, "unscanned:1")
    s = images_svc.vuln_summary(db)
    # Top-level totals stay backward compatible.
    assert s["images"] == 3 and s["scanned_images"] == 2
    assert s["critical"] == 3 and s["high"] == 5
    assert s["running"]["images"] == 1 and s["running"]["scanned_images"] == 1
    assert s["running"]["critical"] == 2 and s["running"]["high"] == 1
    assert s["registry_only"]["images"] == 2
    assert s["registry_only"]["scanned_images"] == 1
    assert s["registry_only"]["critical"] == 1 and s["registry_only"]["high"] == 4
