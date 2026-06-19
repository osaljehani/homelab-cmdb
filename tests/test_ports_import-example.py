import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, ListeningPort
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.ports_import import import_ports, parse_ss


SS = (
    "tcp   LISTEN 0 128  0.0.0.0:22    0.0.0.0:* users:((\"sshd\",pid=812,fd=3))\n"
    "tcp   LISTEN 0 128  127.0.0.1:8080 0.0.0.0:* users:((\"docker-proxy\",pid=99,fd=4))\n"
    "udp   UNCONN 0 0    0.0.0.0:53    0.0.0.0:* users:((\"systemd-resolve\",pid=70,fd=12))\n"
    "tcp   LISTEN 0 128  [::]:22       [::]:*\n"
)


def test_parse_ss_extracts_fields():
    rows = parse_ss(SS)
    assert {"proto": "tcp", "address": "0.0.0.0", "port": 22, "process": "sshd"} in rows
    assert {"proto": "udp", "address": "0.0.0.0", "port": 53,
            "process": "systemd-resolve"} in rows


def test_parse_ss_handles_ipv6_and_missing_process():
    rows = parse_ss(SS)
    v6 = [r for r in rows if r["address"] == "::"]
    assert v6 and v6[0]["port"] == 22 and v6[0]["process"] is None


def test_parse_ss_ignores_blank_and_non_socket_lines():
    assert parse_ss("\n  \nState Recv-Q\n") == []


@pytest.fixture
def host_db(db: Session, host_facts: dict) -> Session:
    import_host(db, host_facts)
    db.flush()
    return db


def test_import_ports_replace_on_import(host_db: Session):
    host = host_db.query(Host).one()
    host_db.add(ListeningPort(host_id=host.id, proto="tcp", address="0.0.0.0",
                              port=9999, process="old"))
    host_db.flush()

    counts = import_ports(host_db, {"host": "testhost", "ports": parse_ss(SS)})

    assert counts["ports"] == 4
    ports = {p.port for p in host_db.query(ListeningPort).all()}
    assert 9999 not in ports  # replaced, not appended
    assert {22, 8080, 53} <= ports


def test_import_ports_unknown_host_raises(host_db: Session):
    with pytest.raises(ValueError):
        import_ports(host_db, {"host": "nope", "ports": []})
