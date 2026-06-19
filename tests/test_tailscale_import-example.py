import json

import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, TailscaleService
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.tailscale_import import (
    import_tailscale,
    parse_tailscale_status,
)

STATUS = json.dumps({
    "Self": {
        "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
        "DNSName": "host-a.example-tailnet.ts.net.",
        "Tags": ["tag:server", "tag:k8s"],
        "ExitNodeOption": True,
        "Online": True,
    }
})

SERVE = json.dumps({
    "Web": {"host-a.example-tailnet.ts.net:443": {
        "Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}}
    }},
    "AllowFunnel": {"host-a.example-tailnet.ts.net:443": True},
})


def test_parse_status_self_fields():
    out = parse_tailscale_status(STATUS, SERVE)
    s = out["self"]
    assert s["ipv4"] == "100.64.0.1"            # IPv4 chosen, not the v6
    assert s["dns_name"] == "host-a.example-tailnet.ts.net"  # trailing dot stripped
    assert s["tags"] == "tag:server,tag:k8s"
    assert s["exit_node"] is True
    assert s["online"] is True


def test_parse_serve_services_with_funnel():
    out = parse_tailscale_status(STATUS, SERVE)
    assert out["services"] == [
        {"proto": "https", "port": 443, "target": "http://127.0.0.1:8080",
         "funnel": True}
    ]


def test_parse_tolerates_empty_and_malformed():
    assert parse_tailscale_status("", "") == {"self": {}, "services": []}
    assert parse_tailscale_status("not json", "{bad") == {"self": {}, "services": []}


def test_parse_serve_tolerates_malformed_nested_shape():
    # Syntactically valid JSON, but Web is not a dict   must not raise.
    out = parse_tailscale_status(STATUS, '{"Web": "unexpected"}')
    assert out["services"] == []


@pytest.fixture
def host_db(db: Session, host_facts: dict) -> Session:
    import_host(db, host_facts)
    db.flush()
    return db


def test_import_tailscale_updates_host_and_services(host_db: Session):
    parsed = parse_tailscale_status(STATUS, SERVE)
    counts = import_tailscale(host_db, {"host": "testhost", **parsed})

    assert counts == {"hosts": 1, "services": 1, "errors": []}
    host = host_db.query(Host).one()
    assert host.tailscale_ipv4 == "100.64.0.1"
    assert host.tailscale_online is True
    assert host_db.query(TailscaleService).count() == 1


def test_import_tailscale_replaces_services(host_db: Session):
    host = host_db.query(Host).one()
    host_db.add(TailscaleService(host_id=host.id, proto="tcp", port=1,
                                 target="x", funnel=False))
    host_db.flush()

    import_tailscale(host_db, {"host": "testhost",
                               **parse_tailscale_status(STATUS, SERVE)})

    svcs = host_db.query(TailscaleService).all()
    assert len(svcs) == 1 and svcs[0].port == 443  # old row replaced


def test_import_tailscale_unknown_host_raises(host_db: Session):
    with pytest.raises(ValueError):
        import_tailscale(host_db, {"host": "nope", "self": {}, "services": []})
