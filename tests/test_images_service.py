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
    assert {i.ref for i in images_svc.list_images(db)} == {"nginx:latest", "noisy-tool:latest"}
    assert {i.ref for i in images_svc.list_images(db, include_noisy=False)} == {"nginx:latest"}


def test_latest_scan_picks_most_recent(db: Session):
    img = _img(db, "nginx:latest", scans=[
        (datetime(2026, 7, 1), 1, 0),
        (datetime(2026, 7, 3), 5, 2),
    ])
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
    img = _img(db, "gone:1", scans=[(datetime(2026, 7, 1), 1, 1), (datetime(2026, 7, 3), 2, 3)])
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


def _scanned(db, ref, last_scanned_at, **kw):
    img = _img(db, ref, scans=[(last_scanned_at, 0, 0)], **kw)
    img.last_scanned_at = last_scanned_at
    db.flush()
    return img


def test_newest_scan_time_is_max_across_all_scans(db: Session):
    _img(db, "a:1", scans=[(datetime(2026, 7, 1), 0, 0)])
    _img(db, "b:1", scans=[(datetime(2026, 7, 5), 0, 0), (datetime(2026, 7, 3), 0, 0)])
    assert images_svc.newest_scan_time(db) == datetime(2026, 7, 5)


def test_newest_scan_time_none_when_no_scans(db: Session):
    _img(db, "unscanned:1")
    assert images_svc.newest_scan_time(db) is None


def test_is_stale_true_when_last_scan_predates_newest(db: Session):
    newest = datetime(2026, 7, 5)
    old = _scanned(db, "gone:1", datetime(2026, 7, 1))
    assert images_svc.is_stale(old, newest) is True


def test_is_stale_false_for_image_in_newest_run(db: Session):
    newest = datetime(2026, 7, 5)
    current = _scanned(db, "nginx:latest", datetime(2026, 7, 5))
    assert images_svc.is_stale(current, newest) is False


def test_is_stale_false_for_never_scanned_image(db: Session):
    newest = datetime(2026, 7, 5)
    never = _img(db, "unscanned:1")  # last_scanned_at is None
    assert images_svc.is_stale(never, newest) is False


def test_is_stale_false_when_no_scans_anywhere(db: Session):
    lone = _scanned(db, "solo:1", datetime(2026, 7, 1))
    assert images_svc.is_stale(lone, images_svc.newest_scan_time(db)) is False


def _host(db, machine_id, hostname):
    h = Host(machine_id=machine_id, hostname=hostname)
    db.add(h)
    db.flush()
    return h


def _container(db, host, name, image):
    db.add(Container(host_id=host.id, name=name, image=image))
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
    assert images_svc.deployments(db, img) == {"docker": [], "source": None}


def test_vuln_summary_excludes_noisy_and_uses_latest(db: Session):
    _img(db, "nginx:latest", scans=[(datetime(2026, 7, 1), 1, 1), (datetime(2026, 7, 3), 2, 3)])
    _img(db, "noisy-tool:latest", noisy=True, scans=[(datetime(2026, 7, 3), 99, 99)])
    _img(db, "unscanned:1")  # no scans
    s = images_svc.vuln_summary(db)
    assert s["images"] == 2          # non-noisy images
    assert s["scanned_images"] == 1  # only nginx has a scan
    assert s["critical"] == 2 and s["high"] == 3 and s["total"] == 5
