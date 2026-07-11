"""Network / subnet map: group hosts by /24 and flag duplicate IPs and MACs."""

from collections import Counter
from ipaddress import ip_address, ip_network

from sqlalchemy.orm import Session

from cmdb.domain.models import Host


def subnet_of(ip: str | None) -> str | None:
    """/24 the address belongs to — a heuristic that fits flat homelab LANs."""
    if not ip:
        return None
    try:
        return str(ip_network(f"{ip_address(ip)}/24", strict=False))
    except ValueError:
        return None


def _sort_key(ip: str) -> tuple:
    try:
        return (0, int(ip_address(ip)))
    except ValueError:
        return (1, 0)


def network_map(session: Session) -> dict:
    """Group hosts by /24 subnet with duplicate-IP/MAC detection.

    Returns ``{"subnets": [{"subnet", "gateway", "hosts": [...]}, ...],
    "unassigned": [...], "duplicate_ips": {ip: [hostnames]},
    "duplicate_macs": {mac: [hostnames]}}``. Subnets sort by network address,
    hosts numerically by IP; the subnet gateway is the most common member
    gateway. MACs compare case-insensitively.
    """
    hosts = session.query(Host).order_by(Host.hostname).all()

    ip_owners: dict[str, list[str]] = {}
    mac_owners: dict[str, list[str]] = {}
    for h in hosts:
        if h.primary_ipv4:
            ip_owners.setdefault(h.primary_ipv4, []).append(h.hostname)
        if h.primary_mac:
            mac_owners.setdefault(h.primary_mac.lower(), []).append(h.hostname)
    duplicate_ips = {ip: names for ip, names in ip_owners.items() if len(names) > 1}
    duplicate_macs = {m: names for m, names in mac_owners.items() if len(names) > 1}

    by_subnet: dict[str, list[Host]] = {}
    unassigned: list[dict] = []
    for h in hosts:
        subnet = subnet_of(h.primary_ipv4)
        if subnet is None:
            unassigned.append({"hostname": h.hostname, "interface": h.primary_interface})
            continue
        by_subnet.setdefault(subnet, []).append(h)

    subnets = []
    for subnet in sorted(by_subnet, key=lambda s: int(ip_network(s).network_address)):
        members = by_subnet[subnet]
        gateways = Counter(h.gateway for h in members if h.gateway)
        rows = [
            {
                "hostname": h.hostname,
                "ip": h.primary_ipv4,
                "mac": h.primary_mac,
                "interface": h.primary_interface,
                "dup_ip": h.primary_ipv4 in duplicate_ips,
                "dup_mac": bool(h.primary_mac)
                and h.primary_mac.lower() in duplicate_macs,
            }
            for h in members
        ]
        rows.sort(key=lambda r: _sort_key(r["ip"]))
        subnets.append(
            {
                "subnet": subnet,
                "gateway": gateways.most_common(1)[0][0] if gateways else None,
                "hosts": rows,
            }
        )

    return {
        "subnets": subnets,
        "unassigned": unassigned,
        "duplicate_ips": duplicate_ips,
        "duplicate_macs": duplicate_macs,
    }
