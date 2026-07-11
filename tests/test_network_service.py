from cmdb.domain.models import Host
from cmdb.domain.services.network import network_map, subnet_of


def _host(db, i, ip=None, mac=None, gateway=None, interface="eth0"):
    h = Host(
        machine_id=f"m{i:032d}",
        hostname=f"host-{i}",
        primary_ipv4=ip,
        primary_mac=mac,
        gateway=gateway,
        primary_interface=interface,
    )
    db.add(h)
    db.flush()
    return h


def test_subnet_of_derives_slash24():
    assert subnet_of("192.168.1.10") == "192.168.1.0/24"
    assert subnet_of(None) is None
    assert subnet_of("not-an-ip") is None


def test_groups_hosts_by_subnet_sorted_by_ip(db):
    _host(db, 1, ip="192.168.1.20", gateway="192.168.1.1")
    _host(db, 2, ip="192.168.1.3", gateway="192.168.1.1")
    _host(db, 3, ip="10.0.0.5", gateway="10.0.0.1")

    nm = network_map(db)

    assert [s["subnet"] for s in nm["subnets"]] == ["10.0.0.0/24", "192.168.1.0/24"]
    lan = nm["subnets"][1]
    assert lan["gateway"] == "192.168.1.1"
    # numeric IP sort, not lexical (3 < 20)
    assert [h["ip"] for h in lan["hosts"]] == ["192.168.1.3", "192.168.1.20"]


def test_gateway_is_mode_of_members(db):
    _host(db, 1, ip="192.168.1.10", gateway="192.168.1.1")
    _host(db, 2, ip="192.168.1.11", gateway="192.168.1.1")
    _host(db, 3, ip="192.168.1.12", gateway="192.168.1.254")

    nm = network_map(db)
    assert nm["subnets"][0]["gateway"] == "192.168.1.1"


def test_duplicate_ips_flagged(db):
    _host(db, 1, ip="192.168.1.10")
    _host(db, 2, ip="192.168.1.10")
    _host(db, 3, ip="192.168.1.11")

    nm = network_map(db)
    assert nm["duplicate_ips"] == {"192.168.1.10": ["host-1", "host-2"]}
    lan_hosts = {h["hostname"]: h for h in nm["subnets"][0]["hosts"]}
    assert lan_hosts["host-1"]["dup_ip"] is True
    assert lan_hosts["host-3"]["dup_ip"] is False


def test_duplicate_macs_case_insensitive(db):
    _host(db, 1, ip="192.168.1.10", mac="AA:BB:CC:00:11:22")
    _host(db, 2, ip="192.168.1.11", mac="aa:bb:cc:00:11:22")

    nm = network_map(db)
    assert nm["duplicate_macs"] == {"aa:bb:cc:00:11:22": ["host-1", "host-2"]}
    assert all(h["dup_mac"] for h in nm["subnets"][0]["hosts"])


def test_hosts_without_ip_land_in_unassigned(db):
    _host(db, 1, ip="192.168.1.10")
    _host(db, 2)  # no IP

    nm = network_map(db)
    assert [h["hostname"] for h in nm["unassigned"]] == ["host-2"]


def test_empty_db(db):
    nm = network_map(db)
    assert nm == {
        "subnets": [],
        "unassigned": [],
        "duplicate_ips": {},
        "duplicate_macs": {},
    }
