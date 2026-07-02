from datetime import datetime

import pytest

from cmdb.domain.models import Container, Host, Image
from cmdb.domain.services.search import global_search


@pytest.fixture
def searchable(db):
    hosts = [
        Host(
            machine_id=f"m{i:032d}",
            hostname=name,
            fqdn=f"{name}.local",
            primary_ipv4=ip,
            tailscale_ipv4=ts,
            os_distribution="Ubuntu",
        )
        for i, (name, ip, ts) in enumerate(
            [
                ("webserver", "192.168.1.10", "100.64.0.1"),
                ("dbserver", "192.168.1.20", None),
                ("mediabox", "192.168.1.30", None),
            ]
        )
    ]
    db.add_all(hosts)
    db.flush()
    db.add_all(
        [
            Container(host_id=hosts[2].id, name="jellyfin", image="jellyfin/jellyfin:latest", state="running"),
            Container(host_id=hosts[0].id, name="nginx-proxy", image="nginx:1.25", state="running"),
        ]
    )
    db.add(Image(ref="nginx:1.25", first_seen=datetime.utcnow()))
    db.commit()
    return hosts


def test_search_by_hostname(db, searchable):
    hits = global_search(db, "webserver")
    assert any(h.kind == "host" and h.label == "webserver" for h in hits)
    assert hits[0].url == "/hosts/webserver"


def test_search_case_insensitive(db, searchable):
    assert any(h.label == "webserver" for h in global_search(db, "WEBSERVER"))


def test_search_by_ip(db, searchable):
    hits = global_search(db, "192.168.1.20")
    assert any(h.kind == "host" and h.label == "dbserver" for h in hits)


def test_search_by_tailscale_ip(db, searchable):
    hits = global_search(db, "100.64.0.1")
    assert any(h.kind == "host" and h.label == "webserver" for h in hits)


def test_search_by_container_name(db, searchable):
    hits = global_search(db, "jellyfin")
    container_hits = [h for h in hits if h.kind == "container"]
    assert container_hits and container_hits[0].label == "jellyfin"
    assert "mediabox" in container_hits[0].sublabel


def test_search_by_image_ref(db, searchable):
    hits = global_search(db, "nginx")
    kinds = {h.kind for h in hits}
    # nginx matches a container (by image string) and an Image row
    assert "image" in kinds and "container" in kinds


def test_search_orders_hosts_first(db, searchable):
    # "server" matches two hosts; hosts come before other kinds
    hits = global_search(db, "server")
    assert hits and hits[0].kind == "host"


def test_search_blank_and_short_queries_return_nothing(db, searchable):
    assert global_search(db, "") == []
    assert global_search(db, " ") == []
    assert global_search(db, "a") == []


def test_search_respects_per_kind_limit(db, searchable):
    for i in range(10):
        db.add(Host(machine_id=f"x{i:032d}", hostname=f"node-{i:02d}"))
    db.commit()
    hits = global_search(db, "node-", limit_per_kind=5)
    assert len([h for h in hits if h.kind == "host"]) == 5
